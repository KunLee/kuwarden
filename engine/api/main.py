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

import json
import os
import uuid
from typing import Annotated, Any, Literal

from fastapi import Body, Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

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
from engine.config import ConfigError, RepoConfig, TriggerConfig, load
from engine.db import connect
from engine.devenv import load_dotenv
from engine.errors import AdapterError, KuWardenError
from engine.evidence import RunNotFound, assemble

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

        from engine.flows.delivery import FlowInput
        from engine.state import Ticket
        from engine.worker import TASK_QUEUE, namespace, target

        client = await Client.connect(target(), namespace=namespace())
    except Exception as exc:  # noqa: BLE001 - Temporal being down is an operational fact
        raise HTTPException(503, f"the Flow Engine is unreachable: {exc}") from None

    run_id = uuid.uuid4()
    handle = await client.start_workflow(
        "DeliveryFlow",
        FlowInput(
            run_id=run_id,
            app_id=app_id,
            # Placeholder. Triage replaces this by reading the real ticket -- what is passed
            # here only identifies which ticket to fetch.
            ticket=Ticket(id=body.ticket_id, system=chosen["provider"], title="", body=""),
            policy_commit=_policy_commit(),
            policy_bundle={"source": "not-loaded"},
            provisional_risk_tier="low",
        ),
        id=f"kuwarden-{run_id}",
        task_queue=TASK_QUEUE,
    )
    return {
        "run_id": str(run_id),
        "workflow_id": handle.id,
        "started_by": principal.email,
    }


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
    repo = _repo_config(repo_url)
    adapter = scm_adapter(repo, store)
    try:
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

    repo = _repo_config(str(row["repo_url"]))
    store = EncryptedPostgresStore(app_id)
    try:
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
    """
    parts = [part for part in repo_url.rstrip("/").split("/") if part]
    if parts and parts[-1].endswith(".git"):
        parts[-1] = parts[-1].removesuffix(".git")
    if "github.com" in repo_url:
        return RepoConfig(name=parts[-1], provider="github", org=parts[-2], repo=parts[-1])
    # https://dev.azure.com/{org}/{project}/_git/{repo}
    return RepoConfig(
        name=parts[-1], provider="azure_repos", org=parts[-4], project=parts[-3], repo=parts[-1]
    )


# --- observe -------------------------------------------------------------------------------


@app.get("/api/runs")
async def list_runs(_: Viewer, limit: int = 50) -> list[dict[str, Any]]:
    async with connect() as conn:
        rows = await conn.fetch(
            "SELECT id, app_id, ticket_system, ticket_id, risk_tier, status, policy_commit, "
            "created_at FROM flow_runs ORDER BY created_at DESC LIMIT $1",
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

    handle = client.get_workflow_handle(f"kuwarden-{run_id}")
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
