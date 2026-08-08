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

import uuid
from typing import Annotated, Any

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from engine.adapters.credentials import CredentialKind, Secret
from engine.adapters.factory import scm_adapter
from engine.adapters.protocols import IntegrationModel, validate_integration_model
from engine.adapters.secrets import EncryptedPostgresStore
from engine.config import RepoConfig
from engine.db import connect
from engine.devenv import load_dotenv
from engine.errors import AdapterError, KuWardenError

load_dotenv()

app = FastAPI(
    title="KuWarden Workbench",
    description="Register applications, hold their credentials, watch their runs.",
    version="0.1.0",
)


# --- request models -------------------------------------------------------------------------


class RegisterApplication(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    scm_provider: str = Field(pattern="^(github|azure_repos)$")
    org: str
    repo: str
    project: str | None = None
    # No default. ADR 0004: never inferred, never defaulted.
    integration_model: IntegrationModel


class StoreCredential(BaseModel):
    # Named `value` rather than `secret` so it is obvious in a log config which field to
    # redact. It is never echoed back by any endpoint here.
    value: str = Field(min_length=1)


# --- register ------------------------------------------------------------------------------


@app.post("/api/applications", status_code=201)
async def register_application(body: RegisterApplication) -> dict[str, Any]:
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


@app.get("/api/applications")
async def list_applications() -> list[dict[str, Any]]:
    async with connect() as conn:
        rows = await conn.fetch(
            "SELECT id, name, repo_url, integration_model, created_at "
            "FROM app_registry ORDER BY created_at DESC"
        )
    return [dict(row) | {"id": str(row["id"])} for row in rows]


# --- credentials ---------------------------------------------------------------------------


@app.get("/api/applications/{app_id}/credentials")
async def list_credentials(app_id: uuid.UUID) -> dict[str, Any]:
    """Which credentials exist. Never their values — there is no endpoint that returns one."""
    store = EncryptedPostgresStore(app_id)
    present = await store.kinds_present(app_id)
    return {
        "present": [kind.value for kind in present],
        "supported": [kind.value for kind in CredentialKind],
    }


@app.put("/api/applications/{app_id}/credentials/{kind}", status_code=204)
async def store_credential(
    app_id: uuid.UUID, kind: str, body: Annotated[StoreCredential, Body()]
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
async def forget_credential(app_id: uuid.UUID, kind: str) -> None:
    try:
        credential_kind = CredentialKind(kind)
    except ValueError:
        raise HTTPException(422, f"unknown credential kind {kind!r}") from None
    await EncryptedPostgresStore(app_id).forget(app_id, credential_kind)


# --- probe ---------------------------------------------------------------------------------


@app.post("/api/applications/{app_id}/probe")
async def probe(app_id: uuid.UUID) -> dict[str, Any]:
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
    parts = repo_url.rstrip("/").split("/")
    if "github.com" in repo_url:
        return RepoConfig(name=parts[-1], provider="github", org=parts[-2], repo=parts[-1])
    # https://dev.azure.com/{org}/{project}/_git/{repo}
    return RepoConfig(
        name=parts[-1], provider="azure_repos", org=parts[-4], project=parts[-3], repo=parts[-1]
    )


# --- observe -------------------------------------------------------------------------------


@app.get("/api/runs")
async def list_runs(limit: int = 50) -> list[dict[str, Any]]:
    async with connect() as conn:
        rows = await conn.fetch(
            "SELECT id, ticket_system, ticket_id, risk_tier, status, policy_commit, created_at "
            "FROM flow_runs ORDER BY created_at DESC LIMIT $1",
            limit,
        )
    return [dict(row) | {"id": str(row["id"])} for row in rows]


@app.get("/api/runs/{run_id}/events")
async def list_events(run_id: uuid.UUID) -> list[dict[str, Any]]:
    async with connect() as conn:
        rows = await conn.fetch(
            "SELECT seq, kind, node_id, control_mode, occurred_at "
            "FROM flow_events WHERE run_id = $1 ORDER BY seq",
            run_id,
        )
    return [dict(row) for row in rows]


@app.get("/api/health")
async def health() -> dict[str, str]:
    async with connect() as conn:
        await conn.fetchval("SELECT 1")
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    from pathlib import Path

    return (Path(__file__).parent / "workbench.html").read_text(encoding="utf-8")
