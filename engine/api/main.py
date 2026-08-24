"""The Workbench API.

Three faces, per the shape agreed: **register** an application, **govern** what it may do,
**observe** what it did. This is the first of them.

Two rules the endpoints here obey and the UI cannot override:

**Credentials are write-only.** A stored value can be replaced or deleted, and its presence
can be reported. It can never be read back. A credential retrievable through a UI is a
credential that eventually is.

**`integration_model` is declared, then validated.** ADR 0004 is explicit that which control
point governs a deployment is a governance decision, not an inference — so the probe may
refuse a declaration, and may not make one.
"""

from __future__ import annotations

import hmac
import json
import os
import uuid
from typing import Annotated, Any, Literal

from fastapi import Body, Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.sessions import SessionMiddleware
from temporalio.exceptions import WorkflowAlreadyStartedError

from engine import config_store
from engine.adapters.credentials import CredentialKind, Secret
from engine.adapters.factory import scm_adapter, ticket_adapter
from engine.adapters.protocols import IntegrationModel, validate_integration_model
from engine.adapters.secrets import EncryptedPostgresStore
from engine.api.auth import (
    SESSION_COOKIE,
    SESSION_MAX_AGE_S,
    Principal,
    Role,
    authenticate,
    create_user,
    current_principal,
    requires,
    session_signing_key,
    user_count,
)
from engine.config import ConfigError, RepoConfig, TriggerConfig, load, parse
from engine.db import connect
from engine.devenv import load_dotenv
from engine.errors import AdapterError, KuWardenError
from engine.evidence import RunNotFound, assemble
from engine.state import RiskRules

load_dotenv()

app = FastAPI(
    title="KuWarden Workbench",
    description="Register applications, hold their credentials, watch their runs.",
    version="0.1.0",
)

# Signed cookie sessions. `https_only` is off for local development over http; a deployment
# behind TLS must set KUWARDEN_HTTPS_ONLY so the cookie is never sent in the clear.
app.add_middleware(
    SessionMiddleware,
    secret_key=session_signing_key(),
    session_cookie=SESSION_COOKIE,
    max_age=SESSION_MAX_AGE_S,
    same_site="lax",
    https_only=os.environ.get("KUWARDEN_HTTPS_ONLY", "").lower() in {"1", "true", "yes"},
)

# Read for anyone signed in, act for approvers, configure for admins. Applied per endpoint
# rather than globally: a new route without a role is then a visible omission in review, not
# an accidental grant.
Viewer = Annotated[Principal, Depends(requires(Role.VIEWER))]
Approver = Annotated[Principal, Depends(requires(Role.APPROVER))]
Admin = Annotated[Principal, Depends(requires(Role.ADMIN))]


# --- request models -------------------------------------------------------------------------


class RegisterApplication(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    scm_provider: str = Field(pattern="^(github|azure_repos)$")
    org: str
    repo: str
    project: str | None = None
    # No default. ADR 0004: never inferred, never defaulted.
    integration_model: IntegrationModel


class ChangeControlPoint(BaseModel):
    """Move an application's control point after registration — ADR 0004.

    `reason` is required and non-empty. This is a governance change, and the whole value of
    recording it is that the record says *why*; a change log full of blank reasons is a list
    of timestamps.
    """

    integration_model: IntegrationModel
    reason: str = Field(min_length=1, max_length=500)


class DeclareTrigger(BaseModel):
    """Which tickets an application accepts.

    Mirrors the `triggers` block of `kuwarden.yaml` rather than inventing a form-shaped
    schema, so generating that file from these rows later is a serialisation and not a
    translation.
    """

    provider: Literal["jira", "azure_devops"]
    project: str = Field(min_length=1)
    site: str | None = None
    account_email: str | None = None
    organisation: str | None = None
    #: Admission control. None means every ticket in the project qualifies -- a decision, not
    #: a default, so the UI states it rather than leaving the field blank and silent.
    label: str | None = None
    #: The workflow state that means "go" — "Ready for Agent". None means state is not
    #: checked. A save fires on every field change; a state transition is a deliberate act,
    #: which is the difference between reading an intention and inferring one.
    ready_state: str | None = None
    max_story_points: int | None = None
    #: No default: the custom field id differs per Jira instance, and guessing reads the
    #: wrong field or nothing at all.
    story_points_field: str | None = None


class StoreCredential(BaseModel):
    # Named `value` rather than `secret` so it is obvious in a log config which field to
    # redact. It is never echoed back by any endpoint here.
    value: str = Field(min_length=1)


# --- session and users ---------------------------------------------------------------------


class Credentials(BaseModel):
    email: str
    password: str


class NewUser(BaseModel):
    email: str = Field(min_length=3)
    display_name: str = Field(min_length=1)
    password: str = Field(min_length=12)
    role: Role


@app.post("/api/session")
async def sign_in(body: Credentials, request: Request) -> dict[str, Any]:
    """Sign in.

    One message for every kind of failure. Distinguishing "no such user" from "wrong
    password" hands an unauthenticated caller an account enumeration oracle.
    """
    principal = await authenticate(body.email, body.password)
    if principal is None:
        raise HTTPException(401, "email or password is incorrect")

    async with connect() as conn:
        version = await conn.fetchval("SELECT token_version FROM users WHERE id = $1", principal.id)

    # Session fixation: a fresh session id for a newly authenticated caller.
    request.session.clear()
    request.session["user_id"] = str(principal.id)
    request.session["token_version"] = version
    return _principal_json(principal)


@app.delete("/api/session", status_code=204)
async def sign_out(request: Request) -> None:
    request.session.clear()


@app.get("/api/session")
async def whoami(principal: Annotated[Principal, Depends(current_principal)]) -> dict[str, Any]:
    return _principal_json(principal)


@app.get("/api/bootstrap")
async def bootstrap_state() -> dict[str, Any]:
    """Whether any account exists.

    Unauthenticated on purpose, and it returns a single boolean. It lets the sign-in page
    explain an empty deployment instead of rejecting every attempt with no explanation, and
    leaks nothing beyond "somebody has set this up".
    """
    return {"configured": await user_count() > 0}


@app.get("/api/users")
async def list_users(_: Admin) -> list[dict[str, Any]]:
    async with connect() as conn:
        rows = await conn.fetch(
            "SELECT id, email, display_name, role, disabled_at, created_at, last_login_at "
            "FROM users ORDER BY created_at"
        )
    return [dict(row) | {"id": str(row["id"])} for row in rows]


@app.post("/api/users", status_code=201)
async def add_user(body: NewUser, _: Admin) -> dict[str, Any]:
    async with connect() as conn:
        clash = await conn.fetchval(
            "SELECT 1 FROM users WHERE email = $1", body.email.strip().lower()
        )
    if clash:
        raise HTTPException(409, f"{body.email} already has an account")
    try:
        user_id = await create_user(body.email, body.display_name, body.password, body.role)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return {"id": str(user_id)}


@app.post("/api/users/{user_id}/disable", status_code=204)
async def disable_user(user_id: uuid.UUID, principal: Admin) -> None:
    """Disable an account and end its sessions immediately.

    Bumping `token_version` is what makes revocation take effect now rather than whenever the
    cookie expires — the same deny-wins instinct as ADR 0003 §5.
    """
    if user_id == principal.id:
        # Otherwise the last admin can lock everyone out of their own deployment, and the
        # recovery path is a CLI nobody remembers exists.
        raise HTTPException(409, "you cannot disable your own account")

    async with connect() as conn:
        admins = await conn.fetchval(
            "SELECT count(*) FROM users WHERE role = 'admin' AND disabled_at IS NULL"
        )
        target_is_admin = await conn.fetchval(
            "SELECT 1 FROM users WHERE id = $1 AND role = 'admin' AND disabled_at IS NULL",
            user_id,
        )
        if target_is_admin and admins <= 1:
            raise HTTPException(409, "this is the only active admin")

        await conn.execute(
            "UPDATE users SET disabled_at = now(), token_version = token_version + 1 "
            "WHERE id = $1",
            user_id,
        )


def _principal_json(principal: Principal) -> dict[str, Any]:
    return {
        "id": str(principal.id),
        "email": principal.email,
        "display_name": principal.display_name,
        "role": principal.role.value,
    }


# --- register ------------------------------------------------------------------------------


@app.post("/api/applications", status_code=201)
async def register_application(body: RegisterApplication, _: Admin) -> dict[str, Any]:
    if body.scm_provider == "azure_repos" and not body.project:
        raise HTTPException(422, "azure_repos requires a project")

    app_id = uuid.uuid4()
    repo_url = (
        f"https://github.com/{body.org}/{body.repo}"
        if body.scm_provider == "github"
        else f"https://dev.azure.com/{body.org}/{body.project}/_git/{body.repo}"
    )
    async with connect() as conn:
        exists = await conn.fetchval("SELECT 1 FROM app_registry WHERE name = $1", body.name)
        if exists:
            raise HTTPException(409, f"an application named {body.name!r} is already registered")
        await conn.execute(
            "INSERT INTO app_registry (id, name, repo_url, integration_model) "
            "VALUES ($1,$2,$3,$4)",
            app_id,
            body.name,
            repo_url,
            body.integration_model.value,
        )
    return {"id": str(app_id), "name": body.name, "repo_url": repo_url}


@app.patch("/api/applications/{app_id}/control-point")
async def change_control_point(
    app_id: uuid.UUID, body: ChangeControlPoint, principal: Admin
) -> dict[str, Any]:
    """Move the control point, and record that it moved.

    Previously impossible: there was no update endpoint, and `DELETE` is refused once a run
    exists, so a mistyped control point was permanent. That was not a decision — it fell out
    of two unrelated ones.

    The change is written to the append-only `app_changes` table in the same transaction. It
    has to be, because `flow_runs` does not record which integration model governed a run:
    without this row, changing the model silently re-interprets every past run under a control
    point that was not in force at the time. Pinning the effective configuration into each run
    is the real fix and is owed; this is the compensating control until then.
    """
    async with connect() as conn, conn.transaction():
        current = await conn.fetchval(
            "SELECT integration_model FROM app_registry WHERE id = $1 FOR UPDATE", app_id
        )
        if current is None:
            raise HTTPException(404, "no such application")
        if current == body.integration_model.value:
            # Not an error, and deliberately not a recorded change: a no-op entry in a change
            # log is noise that makes the real entries harder to find.
            return {"integration_model": current, "changed": False}

        runs = await conn.fetchval("SELECT count(*) FROM flow_runs WHERE app_id = $1", app_id)
        await conn.execute(
            "UPDATE app_registry SET integration_model = $2 WHERE id = $1",
            app_id,
            body.integration_model.value,
        )
        await conn.execute(
            "INSERT INTO app_changes (app_id, field, old_value, new_value, changed_by, reason) "
            "VALUES ($1, 'integration_model', $2, $3, $4, $5)",
            app_id,
            current,
            body.integration_model.value,
            # The authenticated principal, never a client-supplied value.
            principal.email,
            body.reason,
        )
    return {
        "integration_model": body.integration_model.value,
        "changed": True,
        # Surfaced so the caller learns what they just re-interpreted. Silence here would let
        # someone move the control point under a hundred completed runs without noticing.
        "runs_predating_this_change": int(runs or 0),
    }


@app.get("/api/applications/{app_id}/changes")
async def list_changes(app_id: uuid.UUID, _: Viewer) -> list[dict[str, Any]]:
    """The application's configuration history. Readable by anyone who can see the run."""
    async with connect() as conn:
        rows = await conn.fetch(
            "SELECT field, old_value, new_value, changed_by, reason, changed_at "
            "FROM app_changes WHERE app_id = $1 ORDER BY changed_at DESC",
            app_id,
        )
    return [dict(row) for row in rows]


@app.delete("/api/applications/{app_id}", status_code=204)
async def delete_application(app_id: uuid.UUID, _: Admin) -> None:
    """Deregister an application and destroy its stored credentials.

    Refused while runs exist for it. `flow_runs.app_id` is a foreign key into the audit
    trail, and invariant 9 says that trail is append-only — deleting the application would
    either orphan the history or cascade into deleting it. Neither is acceptable for a record
    an auditor may later ask for.
    """
    async with connect() as conn:
        runs = await conn.fetchval("SELECT count(*) FROM flow_runs WHERE app_id = $1", app_id)
        if runs:
            raise HTTPException(
                409,
                f"{runs} run(s) reference this application; its audit trail cannot be "
                "orphaned. Archive support is owed here.",
            )
        # app_credentials cascades on delete, so the ciphertext goes with it.
        deleted = await conn.execute("DELETE FROM app_registry WHERE id = $1", app_id)
    if deleted.endswith("0"):
        raise HTTPException(404, "no such application")


@app.get("/api/applications")
async def list_applications(_: Viewer) -> list[dict[str, Any]]:
    async with connect() as conn:
        rows = await conn.fetch(
            "SELECT id, name, repo_url, integration_model, created_at "
            "FROM app_registry ORDER BY created_at DESC"
        )
    return [dict(row) | {"id": str(row["id"])} for row in rows]


# --- credentials ---------------------------------------------------------------------------


@app.get("/api/applications/{app_id}/credentials")
async def list_credentials(app_id: uuid.UUID, _: Viewer) -> dict[str, Any]:
    """Which credentials exist. Never their values — there is no endpoint that returns one."""
    store = EncryptedPostgresStore(app_id)
    present = await store.kinds_present(app_id)
    return {
        "present": [kind.value for kind in present],
        "supported": [kind.value for kind in CredentialKind],
    }


@app.put("/api/applications/{app_id}/credentials/{kind}", status_code=204)
async def store_credential(
    app_id: uuid.UUID, kind: str, body: Annotated[StoreCredential, Body()], _: Admin
) -> None:
    try:
        credential_kind = CredentialKind(kind)
    except ValueError:
        raise HTTPException(422, f"unknown credential kind {kind!r}") from None

    async with connect() as conn:
        known = await conn.fetchval("SELECT 1 FROM app_registry WHERE id = $1", app_id)
    if not known:
        raise HTTPException(404, "no such application")

    try:
        await EncryptedPostgresStore(app_id).put(app_id, credential_kind, Secret(body.value))
    except KuWardenError as exc:
        # str(exc) never contains the value: Secret refuses to render itself.
        raise HTTPException(400, str(exc)) from None


@app.delete("/api/applications/{app_id}/credentials/{kind}", status_code=204)
async def forget_credential(app_id: uuid.UUID, kind: str, _: Admin) -> None:
    try:
        credential_kind = CredentialKind(kind)
    except ValueError:
        raise HTTPException(422, f"unknown credential kind {kind!r}") from None
    await EncryptedPostgresStore(app_id).forget(app_id, credential_kind)


# --- triggers ------------------------------------------------------------------------------


class StoreConfig(BaseModel):
    """One application's `kuwarden.yaml`, verbatim."""

    yaml: str = Field(min_length=1, max_length=200_000)


@app.get("/api/applications/{app_id}/config")
async def read_config(app_id: uuid.UUID, _: Viewer) -> dict[str, Any]:
    """The stored configuration, and what the worker would fall back to without one.

    Readable by any viewer: unlike a credential, configuration is the thing an operator has to
    be able to inspect to answer "why did that run behave that way". It holds no secrets by
    design — that is why the schema has no credential fields anywhere.
    """
    async with connect() as conn:
        row = await conn.fetchrow(
            "SELECT yaml, updated_at, updated_by FROM app_config WHERE app_id = $1", app_id
        )
    if row is None:
        return {
            "stored": False,
            "yaml": None,
            "detail": (
                "no stored configuration; runs for this application use the worker's own "
                "KUWARDEN_CONFIG file, which serves one application at a time"
            ),
        }
    return {
        "stored": True,
        "yaml": str(row["yaml"]),
        "updated_at": row["updated_at"],
        "updated_by": str(row["updated_by"]),
    }


@app.put("/api/applications/{app_id}/config")
async def store_config(
    app_id: uuid.UUID, body: StoreConfig, principal: Admin
) -> dict[str, Any]:
    """Store this application's configuration, after parsing it.

    Parsed before it is written, never after. A configuration that only fails when a run picks
    it up fails in the worker, minutes later, to somebody who did not make the change — and
    the run is the expensive place to discover a typo.

    The control point is checked against `app_registry` here too, so the disagreement is
    reported to the person editing rather than to the next run.
    """
    try:
        parsed = parse(body.yaml)
    except ConfigError as exc:
        raise HTTPException(422, f"this does not parse as a kuwarden.yaml: {exc}") from None

    async with connect() as conn:
        registered = await conn.fetchrow(
            "SELECT name, integration_model FROM app_registry WHERE id = $1", app_id
        )
        if registered is None:
            raise HTTPException(404, "no such application")

        declared = IntegrationModel(str(registered["integration_model"]))
        if parsed.integration_model is not declared:
            raise HTTPException(
                409,
                f"this application's control point is {declared.value!r}, but the "
                f"configuration declares {parsed.integration_model.value!r}. The registry is "
                "authoritative — change the control point through the application page if "
                "that is what you meant.",
            )

        await conn.execute(
            "INSERT INTO app_config (app_id, yaml, updated_by) VALUES ($1,$2,$3) "
            "ON CONFLICT (app_id) DO UPDATE SET yaml = EXCLUDED.yaml, "
            "updated_by = EXCLUDED.updated_by, updated_at = now()",
            app_id,
            body.yaml,
            principal.email,
        )

    # The worker caches on the row's timestamp, so this takes effect on the next run without
    # a restart. Dropped here as well because the API and the worker may share a process in
    # development.
    config_store.forget(app_id)
    return {"stored": True, "application": parsed.name}


class SetVerifiers(BaseModel):
    """Which verifiers may stop a change. Absent names keep their current setting."""

    blocking: dict[str, bool] = Field(default_factory=dict)


@app.get("/api/applications/{app_id}/verifiers")
async def read_verifiers(app_id: uuid.UUID, _: Viewer) -> dict[str, Any]:
    """Which of the four may block, and which only advise.

    Readable by any viewer. Whether a gate is armed is exactly the kind of fact an operator
    must be able to see without being able to change it.
    """
    from engine.config import ALL_VERIFIERS

    try:
        config = await config_store.resolve(app_id)
    except (ConfigError, KuWardenError) as exc:
        raise HTTPException(409, str(exc)) from None
    blocking = config.verification.blocking
    return {
        "verifiers": [
            {"name": name, "blocking": name in blocking} for name in ALL_VERIFIERS
        ],
        "advisory": list(config.verification.advisory()),
    }


@app.put("/api/applications/{app_id}/verifiers")
async def set_verifiers(
    app_id: uuid.UUID, body: SetVerifiers, principal: Admin
) -> dict[str, Any]:
    """Arm or disarm a verifier for this application.

    Disarming makes it **advisory**: it still runs, still records its findings, and still
    reaches the audit trail — it simply cannot abort the run. Skipping it outright would save
    a model call and destroy the evidence, which for a product whose value is the record is
    the wrong trade.

    Written into the stored `kuwarden.yaml` rather than a column of its own, so there is one
    representation of an application's configuration and one parser for it.
    """
    from engine.config import ALL_VERIFIERS

    unknown = set(body.blocking) - set(ALL_VERIFIERS)
    if unknown:
        raise HTTPException(
            422, f"unknown verifier(s) {', '.join(sorted(unknown))}"
        )

    async with connect() as conn:
        row = await conn.fetchrow("SELECT yaml FROM app_config WHERE app_id = $1", app_id)
        if row is None:
            raise HTTPException(
                409,
                "this application has no stored configuration yet. Save its kuwarden.yaml "
                "first, then arm or disarm its verifiers.",
            )

        current = parse(str(row["yaml"])).verification.blocking
        wanted = {name: (body.blocking.get(name, name in current)) for name in ALL_VERIFIERS}

        updated = _with_verifiers(str(row["yaml"]), wanted)
        # Parsed before it is written, so a malformed rewrite fails for the person making the
        # change rather than for the next run.
        confirmed = parse(updated).verification
        await conn.execute(
            "UPDATE app_config SET yaml = $2, updated_at = now(), updated_by = $3 "
            "WHERE app_id = $1",
            app_id,
            updated,
            principal.email,
        )

    config_store.forget(app_id)
    return {
        "verifiers": [
            {"name": n, "blocking": n in confirmed.blocking} for n in ALL_VERIFIERS
        ],
        "advisory": list(confirmed.advisory()),
    }


def _with_verifiers(yaml_text: str, wanted: dict[str, bool]) -> str:
    """Rewrite the `verification:` block, leaving the rest of the file untouched.

    A round-trip through a YAML loader would discard every comment in the file, and the
    comments in a `kuwarden.yaml` carry the reasoning for the settings — which is most of what
    makes the file reviewable. So the block is replaced textually and appended when absent.
    """
    nl = chr(10)
    rendered = nl.join(f"    {name}: {str(value).lower()}" for name, value in wanted.items())
    block = (
        "verification:" + nl
        + "  # Which verifiers may stop a change. `false` makes one **advisory**: it still" + nl
        + "  # runs, still records its findings, and still reaches the audit trail — it" + nl
        + "  # simply cannot abort the run. Managed from the application page." + nl
        + "  verifiers:" + nl
        + rendered + nl
    )

    lines = yaml_text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.rstrip() == "verification:"), None)
    if start is None:
        return yaml_text.rstrip(nl) + nl + nl + block

    # Everything indented under the key, plus the comment lines immediately above it — those
    # belong to the block being replaced, and leaving them would strand an explanation next to
    # settings it no longer describes.
    end = start + 1
    while end < len(lines) and (not lines[end].strip() or lines[end].startswith((" ", "\t"))):
        end += 1
    head = start
    while head > 0 and lines[head - 1].lstrip().startswith("#"):
        head -= 1
    return nl.join(lines[:head] + block.rstrip(nl).splitlines() + lines[end:]) + nl


@app.get("/api/applications/{app_id}/triggers")
async def list_triggers(app_id: uuid.UUID, _: Viewer) -> list[dict[str, Any]]:
    async with connect() as conn:
        rows = await conn.fetch(
            "SELECT id, provider, project, site, account_email, organisation, label, "
            "ready_state, max_story_points, story_points_field FROM app_triggers "
            "WHERE app_id = $1 ORDER BY provider, project",
            app_id,
        )
    return [dict(row) | {"id": str(row["id"])} for row in rows]


@app.post("/api/applications/{app_id}/triggers", status_code=201)
async def declare_trigger(
    app_id: uuid.UUID, body: DeclareTrigger, _: Admin
) -> dict[str, Any]:
    """Declare which tickets this application accepts.

    Provider-specific requirements are checked here as well as in the database, so the
    Workbench gets a sentence rather than a constraint-violation string.
    """
    if body.provider == "jira" and not (body.site and body.account_email):
        raise HTTPException(422, "jira triggers need both a site URL and an account email")
    if body.provider == "azure_devops" and not body.organisation:
        raise HTTPException(422, "azure_devops triggers need an organisation")

    trigger_id = uuid.uuid4()
    async with connect() as conn:
        known = await conn.fetchval("SELECT 1 FROM app_registry WHERE id = $1", app_id)
        if not known:
            raise HTTPException(404, "no such application")
        clash = await conn.fetchval(
            "SELECT 1 FROM app_triggers WHERE app_id = $1 AND provider = $2 AND project = $3",
            app_id,
            body.provider,
            body.project,
        )
        if clash:
            raise HTTPException(
                409,
                f"a {body.provider} trigger for project {body.project!r} already exists; "
                "two rules for one project would make 'which rule admitted this ticket' "
                "ambiguous",
            )
        await conn.execute(
            "INSERT INTO app_triggers (id, app_id, provider, project, site, account_email, "
            "organisation, label, ready_state, max_story_points, story_points_field) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)",
            trigger_id,
            app_id,
            body.provider,
            body.project,
            body.site,
            body.account_email,
            body.organisation,
            body.label,
            body.ready_state,
            body.max_story_points,
            body.story_points_field,
        )
    return {"id": str(trigger_id)}


class AmendTrigger(BaseModel):
    """Change an existing trigger's admission rules.

    Identity is not amendable. `provider`, `organisation` and `project` are what decide *which*
    board a rule governs, and editing them in place would silently re-point an existing rule at
    a different one — delete and declare instead, so the change is visible as two acts.

    Only fields present in the request body are applied: `null` clears a rule, and omitting a
    field leaves it alone. Without that distinction there is no way to express "stop requiring
    a ready state", and unsetting one is exactly the amendment somebody will need.

    `extra="forbid"` so an attempt to amend an identity field is refused by name. Pydantic's
    default is to drop unknown keys, which would answer a request to re-point a rule at
    another project with a cheerful 200 and no change made.
    """

    model_config = ConfigDict(extra="forbid")

    label: str | None = None
    ready_state: str | None = None
    max_story_points: int | None = None
    story_points_field: str | None = None


@app.patch("/api/applications/{app_id}/triggers/{trigger_id}")
async def amend_trigger(
    app_id: uuid.UUID, trigger_id: uuid.UUID, body: AmendTrigger, _: Admin
) -> dict[str, Any]:
    """Amend the admission rules on a trigger that already exists.

    Added because the alternative was delete-and-recreate, which is worse than clumsy: the
    application has no ticketing at all in between, so `POST /runs` refuses and a service hook
    404s for as long as the gap lasts. Changing one field should not open a window where the
    application cannot accept work.
    """
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(422, "no fields to change")

    # Column names come from this endpoint's own allow-list and never from the request, so the
    # assembled statement cannot be influenced by the body however it is shaped. Values stay
    # parameterised.
    amendable = ("label", "ready_state", "max_story_points", "story_points_field")
    columns = [name for name in amendable if name in changes]
    if not columns:
        raise HTTPException(422, f"only {', '.join(amendable)} may be amended")

    assignments = ", ".join(f"{name} = ${i}" for i, name in enumerate(columns, start=1))
    values = [changes[name] for name in columns]
    async with connect() as conn:
        updated = await conn.fetchrow(
            f"UPDATE app_triggers SET {assignments} "
            f"WHERE id = ${len(columns) + 1} AND app_id = ${len(columns) + 2} "
            "RETURNING id, provider, project, label, ready_state, max_story_points",
            *values,
            trigger_id,
            app_id,
        )
    if updated is None:
        raise HTTPException(404, "no such trigger for this application")
    return dict(updated) | {"id": str(updated["id"])}


@app.delete("/api/applications/{app_id}/triggers/{trigger_id}", status_code=204)
async def remove_trigger(app_id: uuid.UUID, trigger_id: uuid.UUID, _: Admin) -> None:
    async with connect() as conn:
        await conn.execute(
            "DELETE FROM app_triggers WHERE id = $1 AND app_id = $2", trigger_id, app_id
        )


# --- triggering a run ------------------------------------------------------------------------


class StartRun(BaseModel):
    """Start a run for one ticket, by hand.

    Manual because a webhook needs the engine to be reachable from the ticket system, which a
    laptop and an air-gapped deployment both fail at for different reasons. The webhook
    receiver arrives later and starts the same workflow.
    """

    ticket_id: str = Field(min_length=1)
    provider: Literal["jira", "azure_devops"] | None = None


@app.post("/api/applications/{app_id}/runs", status_code=202)
async def start_run(app_id: uuid.UUID, body: StartRun, principal: Approver) -> dict[str, Any]:
    """Hand a ticket to the Flow Engine.

    Requires the approver role rather than admin: starting work is an operational act, while
    changing what an application *is* — its repository, its credentials, its control point —
    is a configuration one.
    """
    async with connect() as conn:
        app_row = await conn.fetchrow(
            "SELECT name FROM app_registry WHERE id = $1", app_id
        )
        if app_row is None:
            raise HTTPException(404, "no such application")

        triggers = await conn.fetch(
            "SELECT provider, project, label, max_story_points FROM app_triggers "
            "WHERE app_id = $1",
            app_id,
        )

    if not triggers:
        raise HTTPException(
            409,
            f"{app_row['name']} has no ticketing configured, so there is no rule that admits "
            "this ticket. Add a trigger first.",
        )
    chosen = next(
        (row for row in triggers if body.provider is None or row["provider"] == body.provider),
        None,
    )
    if chosen is None:
        raise HTTPException(422, f"no {body.provider} trigger is configured")

    try:
        from temporalio.client import Client

        from engine.worker import namespace, target

        client = await Client.connect(target(), namespace=namespace())
    except Exception as exc:  # noqa: BLE001 - Temporal being down is an operational fact
        raise HTTPException(503, f"the Flow Engine is unreachable: {exc}") from None

    run_id, workflow_id = await _launch(
        client,
        app_id,
        body.ticket_id,
        chosen["provider"],
        f"kuwarden-{uuid.uuid4()}",
        app_name=str(app_row["name"]),
    )
    return {
        "run_id": str(run_id),
        "workflow_id": workflow_id,
        "started_by": principal.email,
    }


async def _launch(
    client: Any,
    app_id: uuid.UUID,
    ticket_id: str,
    provider: str,
    workflow_id: str,
    *,
    app_name: str = "",
    reject_duplicate: bool = False,
) -> tuple[uuid.UUID, str]:
    """Start one DeliveryFlow. Shared by the manual button and the service hook.

    `workflow_id` is the caller's, not generated here, because the two callers need different
    guarantees from it. A human pressing the button twice means it twice, so the manual path
    passes a fresh id. A service hook delivered twice means it once, so the hook derives an id
    from the work item revision.

    `reject_duplicate` is the other half of that, and it is not optional for the hook.
    Temporal's default reuse policy is ALLOW_DUPLICATE, which only refuses an id while the
    previous run is *open* — so a redelivery arriving after the run finished would start a
    second one, which is precisely the case a retrying webhook produces. REJECT_DUPLICATE
    refuses the id for good.
    """
    from temporalio.common import WorkflowIDReusePolicy

    from engine.flows.delivery import FlowInput
    from engine.state import Ticket
    from engine.worker import TASK_QUEUE

    risk_rules, blocking_verifiers = await _governing(app_id)

    run_id = uuid.uuid4()
    handle = await client.start_workflow(
        "DeliveryFlow",
        FlowInput(
            run_id=run_id,
            app_id=app_id,
            # Placeholder. Triage replaces this by reading the real ticket -- what is passed
            # here only identifies which ticket to fetch.
            ticket=Ticket(id=ticket_id, system=provider, title="", body=""),
            policy_commit=_policy_commit(),
            policy_bundle={"source": "not-loaded"},
            provisional_risk_tier="low",
            # Checked in Triage against the kuwarden.yaml the worker loaded. A worker serves
            # one application; without this a run for another reads the wrong repository.
            app_name=app_name,
            # Read once, here, and carried into the workflow as data. Workflow code may not
            # touch the filesystem, and pinning the rules at start means editing
            # kuwarden.yaml mid-run cannot retroactively change a tier already decided.
            risk_rules=risk_rules,
            blocking_verifiers=blocking_verifiers,
        ),
        id=workflow_id,
        task_queue=TASK_QUEUE,
        id_reuse_policy=(
            WorkflowIDReusePolicy.REJECT_DUPLICATE
            if reject_duplicate
            else WorkflowIDReusePolicy.ALLOW_DUPLICATE
        ),
    )
    return run_id, handle.id


# --- the service hook ------------------------------------------------------------------------
#
# The direction that did not exist before. KuWarden already *calls* Azure DevOps — it reads the
# work item and posts the run summary back — and that outbound path proves nothing about this
# one. A trigger needs Azure DevOps to call in, which needs an endpoint, a shared secret, and a
# URL the service can reach.


def _tags(revision: dict[str, Any]) -> set[str]:
    """Tags on the work item *after* the update, lowercased.

    Azure DevOps sends them as one semicolon-separated string — "bug; kuwarden-auto" — with
    inconsistent spacing.
    """
    raw = str(revision.get("fields", {}).get("System.Tags") or "")
    return {tag.strip().casefold() for tag in raw.split(";") if tag.strip()}


@app.post("/api/applications/{app_id}/hooks/azure_devops")
async def azure_devops_hook(
    app_id: uuid.UUID, payload: Annotated[dict[str, Any], Body()], request: Request
) -> dict[str, Any]:
    """Start a run when a work item *transitions into* the ready state.

    Reachable without a session, so the shared secret is the only thing between the internet
    and an endpoint that spends model budget and writes code. Configured through
    `KUWARDEN_WEBHOOK_SECRET`; absent, this endpoint refuses to work rather than accepting
    anonymous calls.

    **A transition, not a save.** Azure DevOps fires `workitem.updated` for every field change
    — a reassignment, a typo fix, a tag. `resource.fields` carries only the fields that
    actually changed, so the presence of `System.State` in it *is* the transition test; a save
    that left the state alone never appears there. Admitting on activity rather than intent is
    the design migration 006 was written to avoid.

    Two hundred, not an error, for anything uninteresting. Azure DevOps retries a failed
    delivery, and retrying a typo fix forever is noise that looks like a fault.
    """
    secret = os.environ.get("KUWARDEN_WEBHOOK_SECRET", "")
    if not secret:
        raise HTTPException(
            503,
            "KUWARDEN_WEBHOOK_SECRET is not set, so this endpoint cannot tell Azure DevOps "
            "from anyone else who found the URL. Set it and restart the API.",
        )
    # compare_digest, not ==: string comparison returns early on the first wrong byte, which
    # leaks the shared secret's prefix to anyone willing to time enough requests.
    presented = request.headers.get("x-kuwarden-token", "")
    if not hmac.compare_digest(presented, secret):
        raise HTTPException(401, "bad or missing X-KuWarden-Token")

    if str(payload.get("eventType") or "") != "workitem.updated":
        return {"started": False, "reason": f"ignoring {payload.get('eventType')!r}"}

    async with connect() as conn:
        trigger = await conn.fetchrow(
            "SELECT t.label, t.ready_state, a.name AS app_name "
            "FROM app_triggers t JOIN app_registry a ON a.id = t.app_id "
            "WHERE t.app_id = $1 AND t.provider = 'azure_devops'",
            app_id,
        )
    if trigger is None:
        raise HTTPException(404, "no azure_devops trigger is configured for this application")
    if not trigger["ready_state"]:
        # Fail closed. Without a ready state every state change qualifies, which is the
        # save-driven trigger this design rejected — and it would spend real model budget
        # discovering that at Triage, once per edit.
        raise HTTPException(
            409,
            "this trigger has no ready_state, so there is no transition to fire on. Set one "
            "in the Workbench before pointing a service hook here.",
        )

    resource = payload.get("resource") or {}
    changed = resource.get("fields") or {}
    state_change = changed.get("System.State") or {}
    new_state = str(state_change.get("newValue") or "")
    if not new_state:
        return {"started": False, "reason": "not a state change"}
    if new_state.casefold() != str(trigger["ready_state"]).casefold():
        return {"started": False, "reason": f"moved to {new_state!r}, which does not admit"}

    # Checked here only to avoid starting a run that Triage would refuse a moment later.
    # Triage re-reads the real work item and enforces label, state and points itself, so this
    # is a cheap filter and never the authority — a payload shape that changes under us costs
    # a wasted run, not an ungoverned one.
    label = str(trigger["label"] or "")
    if label and label.casefold() not in _tags(resource.get("revision") or {}):
        return {"started": False, "reason": f"does not carry the {label!r} tag"}

    # `workItemId` first, then `revision.id`, and never `resource.id`. Those are three
    # different numbers in the same payload: `resource.id` identifies the *update record*, so
    # falling back to it starts a run against whichever ticket happens to share that number.
    # Azure DevOps' own sample payload makes the trap concrete — `workItemId` is 0, which is
    # falsy, `resource.id` is 2, and the work item is 5.
    revision_block = resource.get("revision") or {}
    work_item = resource.get("workItemId") or revision_block.get("id")
    if not work_item:
        raise HTTPException(422, "payload carried neither workItemId nor revision.id")
    revision = resource.get("rev") or revision_block.get("rev") or 0

    try:
        from temporalio.client import Client

        from engine.worker import namespace, target

        client = await Client.connect(target(), namespace=namespace())
    except Exception as exc:  # noqa: BLE001 - Temporal being down is an operational fact
        raise HTTPException(503, f"the Flow Engine is unreachable: {exc}") from None

    # Derived from the work item and the revision that moved it, so a redelivered event is
    # the same id and Temporal rejects it. Idempotency is the server's job here: Azure DevOps
    # retries on any non-2xx and gives no delivery id we could key on instead.
    try:
        run_id, workflow_id = await _launch(
            client,
            app_id,
            str(work_item),
            "azure_devops",
            f"kuwarden-ado-{app_id}-{work_item}-r{revision}",
            app_name=str(trigger["app_name"]),
            # A redelivery must be a no-op even if the first run has already finished.
            reject_duplicate=True,
        )
    except WorkflowAlreadyStartedError:
        return {"started": False, "reason": "already started for this revision"}

    return {"started": True, "run_id": str(run_id), "workflow_id": workflow_id}


async def _governing(app_id: uuid.UUID) -> tuple[RiskRules, tuple[str, ...]]:
    """The tiering rules and blocking verifiers that govern this application's runs.

    Resolved from the *stored* configuration, not the worker's own file — ADR 0008. Reading
    the file here would mean a second application's runs were tiered by the first one's rules,
    which is the single-tenant behaviour per-application configuration exists to end.

    Read once here and carried into `FlowInput` as data, because workflow code is deterministic
    and may not consult configuration at all. Pinning them at start also means editing settings
    mid-run cannot retroactively change a decision already recorded.

    A configuration that cannot be resolved yields no tiering rules and every verifier
    blocking — the strict answer in both directions. The inverse would let a malformed file
    silently disarm the gate.
    """
    from engine.config import ALL_VERIFIERS

    try:
        config = await config_store.resolve(app_id)
    except (ConfigError, KuWardenError, OSError):
        return RiskRules(), ALL_VERIFIERS

    rules = RiskRules(
        high_paths=tuple(config.risk.high_paths),
        medium_paths=tuple(config.risk.medium_paths),
        medium_changed_files=config.risk.medium_changed_files,
        high_changed_files=config.risk.high_changed_files,
    )
    return rules, tuple(sorted(config.verification.blocking))


def _policy_commit() -> str:
    """What to pin as the authorising policy version — ADR 0003 §4.

    There is no `policy.yaml` loader yet, so there is nothing to pin. This returns an obvious
    non-SHA rather than a plausible-looking zero commit: an audit record that says
    `unpinned:no-policy-loader` is honest about a missing control, while forty zeroes reads
    like a real pin to anyone scanning the column.
    """
    return "unpinned:no-policy-loader"


# --- connectivity --------------------------------------------------------------------------


@app.post("/api/applications/{app_id}/check")
async def check_connections(app_id: uuid.UUID, _: Admin) -> dict[str, Any]:
    """Can the stored credentials actually reach each platform?

    Deliberately separate from `/probe`, which answers a *governance* question — is the
    declared control point achievable. Those got conflated, and the result was an operator
    reading "the repository's pipeline cannot be restricted" and wondering whether their token
    was broken. Two questions, two buttons, two answers.

    Read-only on both sides, and each side is reported independently: a working SCM token and
    a broken ticket token is the most common half-configured state, and one combined verdict
    would hide which half.
    """
    async with connect() as conn:
        row = await conn.fetchrow("SELECT repo_url FROM app_registry WHERE id = $1", app_id)
        if row is None:
            raise HTTPException(404, "no such application")
        triggers = await conn.fetch(
            "SELECT provider, site, account_email, organisation, project, ready_state "
            "FROM app_triggers WHERE app_id = $1",
            app_id,
        )

    store = EncryptedPostgresStore(app_id)
    results: dict[str, Any] = {"scm": await _check_scm(str(row["repo_url"]), store)}
    results["model"] = await _check_llm(store)

    if not triggers:
        results["ticket"] = {
            "ok": False,
            "detail": "no ticket trigger is configured for this application",
        }
    else:
        results["ticket"] = await _check_ticket(triggers[0], store)
    return results


async def _check_scm(repo_url: str, store: EncryptedPostgresStore) -> dict[str, Any]:
    """Resolve the default branch. Proves the token, the repository, and that it has a commit."""
    try:
        # Inside the try, so a stored URL that is not a repository URL is reported as a failed
        # check like every other reason this can fail. The point of this endpoint is to name
        # what is wrong with a half-configured application; a 500 names nothing.
        repo = _repo_config(repo_url)
        adapter = scm_adapter(repo, store)
        branch = await adapter.default_branch(repo.ref())
        # Read access is enough for every node up to and including the Coder, so a token
        # missing only the write grant produces a full model run — real tokens, real cost —
        # and then a 403 at Push. Asking here costs one request.
        writable, why = await adapter.write_access(repo.ref())
    except (AdapterError, KuWardenError) as exc:
        return {"ok": False, "target": repo_url, "detail": str(exc)}

    at = f"default branch {branch.name} at {branch.commit[:8]}"
    if writable is False:
        return {"ok": False, "target": repo_url, "detail": f"{at}, but {why}"}
    if writable is None:
        # Not a pass. "Could not check" and "checked and fine" are different facts, and a
        # green tick on the first is the overstatement this whole check exists to avoid.
        return {"ok": True, "target": repo_url, "detail": f"{at}. Write access: {why}"}
    return {"ok": True, "target": repo_url, "detail": f"{at}, and {why}"}


async def _check_llm(store: EncryptedPostgresStore) -> dict[str, Any]:
    """List models. Proves the key without generating anything, so pressing it is free.

    Which provider and model come from `kuwarden.yaml`, not the database — so this reports
    what the *worker* would use, and a check that passed against different configuration
    would be worse than no check.
    """
    from engine.adapters.llm.factory import llm_adapter
    from engine.worker import config_path

    try:
        config = load(config_path())
    except ConfigError as exc:
        return {"ok": False, "target": "kuwarden.yaml", "detail": str(exc)}
    if config.llm is None:
        return {
            "ok": False,
            "target": "kuwarden.yaml",
            "detail": "no llm section is declared; the Planner and Coder both need a model",
        }

    target = f"{config.llm.provider.value}"
    try:
        adapter = llm_adapter(config.llm, "planner", store)
        return {"ok": True, "target": target, "detail": await adapter.ping()}
    except (AdapterError, KuWardenError) as exc:
        return {"ok": False, "target": target, "detail": str(exc)}


async def _check_ticket(row: Any, store: EncryptedPostgresStore) -> dict[str, Any]:
    """Read the configured project. Proves the token *and* that the project name is right."""
    trigger = TriggerConfig(
        provider=str(row["provider"]),
        project=str(row["project"]),
        site=row["site"],
        account_email=row["account_email"],
        organisation=row["organisation"],
    )
    target = f"{trigger.organisation or trigger.site}/{trigger.project}"
    try:
        found = await ticket_adapter(trigger, store).ping(trigger.ref(""))
    except (AdapterError, KuWardenError) as exc:
        return {"ok": False, "target": target, "detail": str(exc)}
    return {"ok": True, "target": target, "detail": f"reached {found}"}


# --- probe ---------------------------------------------------------------------------------


@app.post("/api/applications/{app_id}/probe")
async def probe(app_id: uuid.UUID, _: Admin) -> dict[str, Any]:
    """Ask the platform what it can actually do — ADR 0004 §2.

    Runs against the stored SCM credential, so it also answers "is that token any good".
    """
    async with connect() as conn:
        row = await conn.fetchrow(
            "SELECT name, repo_url, integration_model FROM app_registry WHERE id = $1", app_id
        )
    if row is None:
        raise HTTPException(404, "no such application")

    store = EncryptedPostgresStore(app_id)
    try:
        repo = _repo_config(str(row["repo_url"]))
        capabilities = await scm_adapter(repo, store).probe(repo.ref())
    except (AdapterError, KuWardenError) as exc:
        raise HTTPException(400, f"probe failed: {exc}") from None

    verdict = validate_integration_model(IntegrationModel(row["integration_model"]), capabilities)
    return {
        "declared": row["integration_model"],
        "achievable": verdict.achievable,
        "reason": verdict.reason,
        "capabilities": {
            "deployment_protection": capabilities.deployment_protection,
            "required_status_checks": capabilities.required_status_checks,
            "restrictable_pipeline_triggers": capabilities.restrictable_pipeline_triggers,
            "detail": capabilities.detail,
        },
    }


def _repo_config(repo_url: str) -> RepoConfig:
    """Parse a repository URL as an operator would paste it.

    `.git` is stripped because the clone URL is what a platform's copy button hands you, and
    keeping it would register the repository as `sasagayo.git` — every subsequent API call
    then 404s, which reads like a bad token rather than a bad name.

    Raises `ConfigError` naming the shape that was expected. A URL too short for the fields
    being read used to fall off the end of the list as an `IndexError`, which reaches the
    operator as a 500 with no body — the least actionable failure this endpoint can produce,
    for the most obvious kind of mistake.
    """
    # Split the scheme off rather than filtering it out by name, so a URL pasted without one
    # parses identically. `rpartition` returns the whole string as the tail when there is no
    # separator, which is exactly the wanted behaviour here.
    _, _, authority = repo_url.strip().rstrip("/").rpartition("://")
    segments = [segment for segment in authority.split("/") if segment]
    if segments and segments[-1].endswith(".git"):
        segments[-1] = segments[-1].removesuffix(".git")

    if "github.com" in repo_url:
        # host / org / repo.
        if len(segments) < 3:
            raise ConfigError(
                f"{repo_url!r} is not a GitHub repository URL; expected "
                "https://github.com/<org>/<repo>"
            )
        return RepoConfig(
            name=segments[-1], provider="github", org=segments[-2], repo=segments[-1]
        )

    # host / org / project / _git / repo.
    #
    # `_git` is required rather than assumed: without it, an organisation URL pasted by mistake
    # parses into a plausible-looking RepoConfig built from the wrong segments, and the first
    # sign of trouble is a 404 against a repository nobody named.
    if len(segments) < 5 or segments[-2] != "_git":
        raise ConfigError(
            f"{repo_url!r} is not an Azure Repos URL; expected "
            "https://dev.azure.com/<org>/<project>/_git/<repo>"
        )
    return RepoConfig(
        name=segments[-1],
        provider="azure_repos",
        org=segments[-4],
        project=segments[-3],
        repo=segments[-1],
    )


# --- observe -------------------------------------------------------------------------------


@app.get("/api/runs")
async def list_runs(_: Viewer, limit: int = 50) -> list[dict[str, Any]]:
    """Every run, newest first, with enough context to identify one without opening it.

    `app_name` is joined rather than resolved in the browser: the list is the place an
    operator works out which run they are looking at, and a ticket id alone does not say
    which application it belongs to once more than one is registered.

    LEFT JOIN, not an inner join. `app_id` is NOT NULL with a foreign key, so today the row
    is always there — but this list is a view onto the audit trail, and a run disappearing
    from it because of a join is a failure mode worth spending a `COALESCE` to make
    impossible.
    """
    async with connect() as conn:
        rows = await conn.fetch(
            "SELECT r.id, r.app_id, COALESCE(a.name, '(deleted)') AS app_name, "
            "r.ticket_system, r.ticket_id, r.risk_tier, r.status, r.policy_commit, "
            "r.created_at, r.ended_at "
            "FROM flow_runs r LEFT JOIN app_registry a ON a.id = r.app_id "
            "ORDER BY r.created_at DESC LIMIT $1",
            limit,
        )
    return [
        dict(row) | {"id": str(row["id"]), "app_id": str(row["app_id"])} for row in rows
    ]


@app.get("/api/runs/{run_id}/events")
async def list_events(run_id: uuid.UUID, _: Viewer) -> list[dict[str, Any]]:
    async with connect() as conn:
        rows = await conn.fetch(
            "SELECT seq, kind, node_id, control_mode, payload, occurred_at "
            "FROM flow_events WHERE run_id = $1 ORDER BY seq",
            run_id,
        )
    # payload is JSONB; asyncpg hands it back as a string.
    return [dict(row) | {"payload": json.loads(row["payload"] or "{}")} for row in rows]


# --- the approval gate -----------------------------------------------------------------------


class Decision(BaseModel):
    """An approver's answer, bound to what they were shown.

    `evidence_digest` is not optional and is not a formality. Without it "approved" means
    "someone clicked a button on this run"; with it, it means "approved these exact facts" —
    ADR 0003 §6.
    """

    approved: bool
    evidence_digest: str = Field(min_length=64, max_length=64)
    comment: str = Field(default="", max_length=4000)


@app.get("/api/runs/{run_id}/diagnostics")
async def run_diagnostics(run_id: uuid.UUID, _: Viewer) -> list[dict[str, Any]]:
    """Per-attempt execution detail, from Temporal's workflow history.

    **Not the audit trail, and deliberately a separate endpoint.** `flow_events` is the record
    — append-only, and what a regulator would be shown. Stack traces do not belong in it: they
    are long, they are implementation detail, and they can never be removed once written.

    But the operator debugging a failed run needs them, and today the only way to get one is
    to open the Temporal UI. This brings the same information into the Workbench without
    putting it in the permanent record: Temporal's history is retained on its own schedule,
    so this is diagnosis, not evidence.

    Retention is exactly why the two must not be conflated. This endpoint returns nothing once
    Temporal has expired the history; the audit trail is still there.
    """
    try:
        from temporalio.client import Client

        from engine.worker import namespace, target

        client = await Client.connect(target(), namespace=namespace())
    except Exception as exc:  # noqa: BLE001 - Temporal being down is an operational fact
        raise HTTPException(503, f"the Flow Engine is unreachable: {exc}") from None

    # Read from the row, never rebuilt from the run id. The two are not interchangeable: a
    # run started by the service hook is keyed on the work item and its revision, so
    # reconstructing `kuwarden-{run_id}` looks up a workflow that never existed and reports a
    # missing history for a run whose history is fine.
    async with connect() as conn:
        workflow_id = await conn.fetchval(
            "SELECT workflow_id FROM flow_runs WHERE id = $1", run_id
        )
    if workflow_id is None:
        raise HTTPException(404, "no such run")

    handle = client.get_workflow_handle(workflow_id)
    scheduled: dict[int, str] = {}
    attempts: list[dict[str, Any]] = []

    # Compared against the enum, not against `str(event.event_type)`. The event type is an
    # integer enum whose `str()` is the number, so a name match silently returns nothing —
    # an endpoint that always answered "no failures" for a run that plainly failed.
    from temporalio.api.enums.v1 import EventType

    scheduled_type = EventType.Value("EVENT_TYPE_ACTIVITY_TASK_SCHEDULED")
    failed_type = EventType.Value("EVENT_TYPE_ACTIVITY_TASK_FAILED")
    timed_out_type = EventType.Value("EVENT_TYPE_ACTIVITY_TASK_TIMED_OUT")

    try:
        async for event in handle.fetch_history_events():
            if event.event_type == scheduled_type:
                # `activity_id` is `<node>#<seq>` — set in workflow code precisely so this
                # correlation needs no decoding of the activity's (very large) input.
                #
                # Runs recorded before that naming carry Temporal's bare counter, and a
                # counter cannot be attributed to a node. Reported as unattributed rather
                # than as a node called "6": the caller can then say so, instead of matching
                # nothing and rendering an empty panel over data it actually has.
                attrs = event.activity_task_scheduled_event_attributes
                activity_id = attrs.activity_id
                scheduled[event.event_id] = (
                    activity_id.split("#", 1)[0] if "#" in activity_id else ""
                )
            elif event.event_type == failed_type:
                attrs_failed = event.activity_task_failed_event_attributes
                failure = attrs_failed.failure
                attempts.append(
                    {
                        "node_id": scheduled.get(attrs_failed.scheduled_event_id, ""),
                        "outcome": "failed",
                        "error": failure.application_failure_info.type or "error",
                        "message": failure.message,
                        # Truncated: a full trace is unreadable in a table and the top of it
                        # is where the cause is.
                        "stack_trace": failure.stack_trace[:4000],
                        "at": event.event_time.ToDatetime().isoformat(),
                    }
                )
            elif event.event_type == timed_out_type:
                attrs_timeout = event.activity_task_timed_out_event_attributes
                attempts.append(
                    {
                        "node_id": scheduled.get(attrs_timeout.scheduled_event_id, ""),
                        "outcome": "timed_out",
                        "error": "TimeoutError",
                        "message": str(attrs_timeout.failure.message),
                        "stack_trace": "",
                        "at": event.event_time.ToDatetime().isoformat(),
                    }
                )
    except Exception as exc:  # noqa: BLE001 - an expired history is a normal answer
        raise HTTPException(
            404,
            f"no workflow history for this run: {exc}. Temporal retains history on its own "
            "schedule; the audit trail is unaffected.",
        ) from None

    return attempts


@app.get("/api/runs/{run_id}/evidence")
async def run_evidence(run_id: uuid.UUID, _: Viewer) -> dict[str, Any]:
    """What an approver decides against, and the digest that will bind their decision."""
    try:
        evidence = await assemble(run_id)
    except RunNotFound as exc:
        raise HTTPException(404, str(exc)) from None
    return {"digest": evidence.digest, "document": evidence.document}


@app.post("/api/runs/{run_id}/approval", status_code=202)
async def decide(run_id: uuid.UUID, body: Decision, principal: Approver) -> dict[str, Any]:
    """Record an approver's decision and release the suspended run.

    The digest is recomputed here rather than trusted. A run keeps producing events while it
    waits, so a stale page is a real and ordinary occurrence — and approving against evidence
    that has since changed is precisely the thing the digest exists to prevent.
    """
    try:
        evidence = await assemble(run_id)
    except RunNotFound as exc:
        raise HTTPException(404, str(exc)) from None

    if evidence.digest != body.evidence_digest:
        raise HTTPException(
            409,
            "the evidence changed after this page was loaded, so this decision would be "
            "recorded against something you did not read. Reload and decide again.",
        )

    async with connect() as conn:
        workflow_id = await conn.fetchval(
            "SELECT workflow_id FROM flow_runs WHERE id = $1", run_id
        )
        status = await conn.fetchval("SELECT status FROM flow_runs WHERE id = $1", run_id)
    if status != "suspended":
        raise HTTPException(409, f"this run is {status}; it is not waiting for a decision")

    try:
        from temporalio.client import Client

        from engine.flows.delivery import ApprovalSignal
        from engine.worker import namespace, target

        client = await Client.connect(target(), namespace=namespace())
        handle = client.get_workflow_handle(workflow_id)
        await handle.signal(
            "approve",
            ApprovalSignal(
                # The authenticated identity, never a value the client supplied. An approver
                # naming someone else is the one thing this record must not permit.
                principal=principal.email,
                approved=body.approved,
                evidence_digest=evidence.digest,
                comment=body.comment,
            ),
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - Temporal being down is an operational fact
        raise HTTPException(503, f"the Flow Engine is unreachable: {exc}") from None

    return {"recorded": True, "approved": body.approved, "principal": principal.email}


@app.post("/api/runs/{run_id}/terminate", status_code=202)
async def terminate_run(run_id: uuid.UUID, principal: Admin) -> dict[str, Any]:
    """Stop a run that is going nowhere, and say in the record who stopped it.

    **Terminate, not cancel, and the difference is not cosmetic.** A Temporal cancellation is
    delivered to the workflow as `asyncio.CancelledError`, which is a `BaseException` — the
    flow's `except Exception` handler does not catch it, so compensation would not run and the
    operator would be told cleanup had happened when it had not. Terminating is at least
    honest about being abrupt, and the event below states plainly what was left behind.

    **`admin`, not `approver`.** Rejecting a change at the gate is a judgment about the change
    and belongs to an approver. Killing a run mid-flight is an operational act on the platform
    — it can leave a branch on someone's remote — and is not a decision about the code.

    The branch is deliberately *not* deleted here. This endpoint holds no SCM credential
    (invariant 2 applies to the API as much as to the nodes), and a run stopped by a person is
    exactly the case where somebody may want to look at what the agent produced before it
    disappears. The row says the branch is still there so nobody has to guess.
    """
    async with connect() as conn:
        row = await conn.fetchrow(
            "SELECT status, workflow_id, ticket_id FROM flow_runs WHERE id = $1", run_id
        )
        if row is None:
            raise HTTPException(404, f"no run {run_id}")
        if row["status"] not in ("running", "suspended"):
            raise HTTPException(409, f"this run is {row['status']}; there is nothing to stop")
        # Read before terminating, so the record can name the branch that is being orphaned.
        branch = await conn.fetchval(
            "SELECT payload->>'branch' FROM flow_events "
            "WHERE run_id = $1 AND kind = 'branch_pushed' ORDER BY seq DESC LIMIT 1",
            run_id,
        )
        # The next sequence number, taken from the trail itself. The workflow assigns its own
        # numbers and this row is written from outside it, so continuing the sequence is the
        # only way the event lands in order rather than colliding with seq 1.
        seq = int(await conn.fetchval(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM flow_events WHERE run_id = $1", run_id
        ))

    try:
        from temporalio.client import Client

        from engine.worker import namespace, target

        client = await Client.connect(target(), namespace=namespace())
        await client.get_workflow_handle(str(row["workflow_id"])).terminate(
            reason=f"terminated from the Workbench by {principal.email}"
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - Temporal being down is an operational fact
        raise HTTPException(503, f"the Flow Engine is unreachable: {exc}") from None

    async with connect() as conn:
        # Written after the terminate succeeded, never before. A row saying a run was stopped
        # while it carried on running is worse than no row.
        await conn.execute(
            "INSERT INTO flow_events (run_id, seq, kind, node_id, payload) "
            "VALUES ($1, $2, 'run_terminated', NULL, $3) ON CONFLICT (run_id, seq) DO NOTHING",
            run_id,
            seq,
            json.dumps(
                {
                    "principal": principal.email,
                    "was": row["status"],
                    "detail": "terminated from the Workbench; compensation did not run",
                    # Named rather than implied. Somebody has to delete this by hand.
                    "branch_left_behind": branch,
                }
            ),
        )
        await conn.execute(
            "UPDATE flow_runs SET status = 'terminated', ended_at = now() "
            "WHERE id = $1 AND status IN ('running', 'suspended')",
            run_id,
        )

    return {
        "terminated": True,
        "principal": principal.email,
        "branch_left_behind": branch,
    }


@app.get("/api/sandbox")
async def sandbox_status(_: Viewer) -> dict[str, Any]:
    """What the sandbox host actually enforces.

    Surfaced so the Workbench can say plainly that runs are executing under weakened
    isolation. A degradation that lives only in a log line is a degradation nobody sees.
    """
    from engine.sandbox.podman import PodmanSandbox

    try:
        capabilities = await PodmanSandbox(require_full_isolation=False).capabilities()
    except Exception as exc:  # noqa: BLE001 - the sandbox host is not always present
        return {"available": False, "reason": str(exc), "fully_enforced": False, "gaps": []}

    return {
        "available": True,
        "fully_enforced": capabilities.fully_enforced,
        "gaps": capabilities.gaps(),
        "enforced": {
            "wall_clock": capabilities.wall_clock,
            "network_isolation": capabilities.network_isolation,
            "rlimit_memory": capabilities.rlimit_memory,
            "tmpfs_quota": capabilities.tmpfs_quota,
            "cgroup_memory": capabilities.cgroup_memory,
            "cgroup_cpu": capabilities.cgroup_cpu,
            "cgroup_pids": capabilities.cgroup_pids,
        },
    }


@app.get("/api/health")
async def health() -> dict[str, str]:
    async with connect() as conn:
        await conn.fetchval("SELECT 1")
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    from pathlib import Path

    return (Path(__file__).parent / "workbench.html").read_text(encoding="utf-8")
