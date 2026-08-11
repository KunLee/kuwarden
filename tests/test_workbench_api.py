"""The Workbench API — authorisation, and the rules the UI cannot override.

The role guard is the only thing standing between a viewer and every credential slot in the
deployment, and it is applied per endpoint by hand. That design is deliberate (an unguarded
route is then visible in review) but it means a missing guard is a silent, total failure.
These tests read the route table and assert on it directly, so a new endpoint added without
a role fails here rather than in production.

Runs against the real PostgreSQL from the compose stack, because the guard reads
`token_version` from it on every request and an in-memory stand-in would not exercise that.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi.routing import APIRoute

from engine.api.auth import Role, create_user
from engine.api.main import _repo_config, app
from engine.db import connect

#: A password that satisfies the length rule without being a plausible real one.
PASSWORD = "correct-horse-battery-staple"


@pytest.fixture
async def accounts() -> AsyncIterator[dict[Role, str]]:
    """One account per role, removed afterwards.

    Emails are per-run unique: the suite may run against a database someone else is also
    using, and a collision would look like a bug in `add_user`.
    """
    tag = uuid.uuid4().hex[:8]
    emails = {role: f"{role.value}-{tag}@test.invalid" for role in Role}
    for role, email in emails.items():
        await create_user(email, role.value.title(), PASSWORD, role)
    try:
        yield emails
    finally:
        async with connect() as conn:
            await conn.execute(
                "DELETE FROM users WHERE email = ANY($1::text[])", list(emails.values())
            )


def _client() -> httpx.AsyncClient:
    """Drive the app in-process. No port, no server, same routing and dependencies."""
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://workbench")


@asynccontextmanager
async def _signed_in(email: str) -> AsyncIterator[httpx.AsyncClient]:
    """A client holding a real session cookie, obtained the way a browser obtains one."""
    async with _client() as client:
        response = await client.post("/api/session", json={"email": email, "password": PASSWORD})
        assert response.status_code == 200, response.text
        yield client


# --- the guard ------------------------------------------------------------------------------


def test_every_endpoint_declares_who_may_call_it() -> None:
    """No route reaches the database without a role attached.

    Guards are per endpoint, so the failure mode of forgetting one is an open endpoint rather
    than an error. This enumerates the routes instead of trusting review to catch it.
    """
    #: Endpoints that are unauthenticated on purpose, each with the reason it is safe.
    public = {
        "/api/session": "sign-in itself, and sign-out/whoami which carry their own checks",
        "/api/bootstrap": "returns one boolean: whether any account exists at all",
        "/api/health": "liveness, read by a probe that has no credentials",
        "/openapi.json": "schema",
        "/docs": "schema",
        "/docs/oauth2-redirect": "schema",
        "/redoc": "schema",
        "/": "the Workbench itself",
    }

    unguarded: list[str] = []
    for route in app.routes:
        if not isinstance(route, APIRoute) or route.path in public:
            continue
        # The guard arrives as a dependency on a parameter annotated Viewer/Approver/Admin.
        names = {dep.call.__qualname__ for dep in route.dependant.dependencies if dep.call}
        if not any(name.startswith("requires") for name in names):
            unguarded.append(f"{sorted(route.methods or set())} {route.path}")

    assert not unguarded, (
        "these endpoints have no role guard; add one, or list them in `public` with a "
        f"reason: {unguarded}"
    )


async def test_an_anonymous_caller_is_refused() -> None:
    async with _client() as client:
        assert (await client.get("/api/applications")).status_code == 401


async def test_a_viewer_cannot_register_an_application(accounts: dict[Role, str]) -> None:
    """Read for anyone signed in, configure for admins."""
    async with _signed_in(accounts[Role.VIEWER]) as client:
        assert (await client.get("/api/applications")).status_code == 200

        refused = await client.post(
            "/api/applications",
            json={
                "name": "payments",
                "scm_provider": "github",
                "org": "acme",
                "repo": "payments",
                "integration_model": "gated_deployment",
            },
        )
        assert refused.status_code == 403
        assert "admin" in refused.json()["detail"]


async def test_a_disabled_account_loses_its_session_immediately(
    accounts: dict[Role, str],
) -> None:
    """Revocation must not wait for the cookie to expire — the point of `token_version`."""
    async with _signed_in(accounts[Role.VIEWER]) as client:
        assert (await client.get("/api/applications")).status_code == 200

        async with connect() as conn:
            await conn.execute(
                "UPDATE users SET disabled_at = now(), token_version = token_version + 1 "
                "WHERE email = $1",
                accounts[Role.VIEWER],
            )

        assert (await client.get("/api/applications")).status_code == 401


async def test_sign_in_does_not_say_which_half_was_wrong(accounts: dict[Role, str]) -> None:
    """Distinguishing the two hands an unauthenticated caller an enumeration oracle."""
    async with _client() as client:
        no_user = await client.post(
            "/api/session", json={"email": "nobody@test.invalid", "password": PASSWORD}
        )
        bad_password = await client.post(
            "/api/session", json={"email": accounts[Role.VIEWER], "password": "wrong-" + PASSWORD}
        )

    assert no_user.status_code == bad_password.status_code == 401
    assert no_user.json()["detail"] == bad_password.json()["detail"]


# --- credentials ----------------------------------------------------------------------------


async def test_a_stored_credential_never_comes_back_out(accounts: dict[Role, str]) -> None:
    """Write-only is the whole design. A credential a UI can read is one that eventually is."""
    secret = f"pat-{uuid.uuid4().hex}"
    async with _signed_in(accounts[Role.ADMIN]) as client:
        created = await client.post(
            "/api/applications",
            json={
                "name": f"payments-{uuid.uuid4().hex[:8]}",
                "scm_provider": "github",
                "org": "acme",
                "repo": "payments",
                "integration_model": "gated_deployment",
            },
        )
        assert created.status_code == 201, created.text
        app_id = created.json()["id"]

        stored = await client.put(
            f"/api/applications/{app_id}/credentials/scm.read", json={"value": secret}
        )
        assert stored.status_code in (200, 204), stored.text

        listing = await client.get(f"/api/applications/{app_id}/credentials")
        assert listing.status_code == 200
        body = listing.text
        assert secret not in body, "the endpoint echoed the secret back"
        assert "scm.read" in body, "but its presence must still be reportable"

        await client.delete(f"/api/applications/{app_id}")


# --- the approval gate ----------------------------------------------------------------------


@asynccontextmanager
async def _run_at_gate(verdict: str) -> AsyncIterator[uuid.UUID]:
    """A run parked at its gate, carrying one `build_test_verdict` payload.

    Inserted directly rather than driven through the workflow: what is under test here is the
    binding between an approver and the document they read, and running a real flow to reach
    the same rows would make these tests take a minute to say the same thing.

    The verdict is a parameter because *who graded the change* is what the caveats turn on,
    and both answers need a test — a caveat that never disappears is indistinguishable from
    one that is hard-coded.
    """
    app_id, run_id = uuid.uuid4(), uuid.uuid4()
    async with connect() as conn:
        await conn.execute(
            "INSERT INTO app_registry (id, name, repo_url, integration_model) "
            "VALUES ($1,$2,$3,'gated_deployment')",
            app_id,
            f"evidence-{run_id.hex[:8]}",
            "https://github.com/acme/payments",
        )
        await conn.execute(
            "INSERT INTO flow_runs (id, root_run_id, app_id, workflow_id, ticket_system, "
            "ticket_id, risk_tier, status, schema_version, policy_commit, policy_bundle) "
            "VALUES ($1,$1,$2,$3,'jira','PAY-1234','high','suspended',1,$4,'{}'::jsonb)",
            run_id,
            app_id,
            f"kuwarden-{run_id}",
            "unpinned:no-policy-loader",
        )
        await conn.execute(
            "INSERT INTO flow_events (run_id, seq, kind, node_id, payload) "
            "VALUES ($1, 1, 'build_test_verdict', 'build_test', $2::jsonb)",
            run_id,
            verdict,
        )
    try:
        yield run_id
    finally:
        async with connect() as conn:
            # flow_events has an append-only trigger (invariant 9), so the table is dropped
            # from under it rather than deleted through it. Only ever acceptable in a test.
            await conn.execute("ALTER TABLE flow_events DISABLE TRIGGER flow_events_no_update")
            await conn.execute("DELETE FROM flow_events WHERE run_id = $1", run_id)
            await conn.execute("ALTER TABLE flow_events ENABLE TRIGGER flow_events_no_update")
            await conn.execute("DELETE FROM flow_runs WHERE id = $1", run_id)
            await conn.execute("DELETE FROM app_registry WHERE id = $1", app_id)


@pytest.fixture
async def suspended_run() -> AsyncIterator[uuid.UUID]:
    """Graded by KuWarden's own sandbox, with no CI verdict available."""
    async with _run_at_gate(
        '{"exit_code": 0, "source": "sandbox", "independent_anchor": false, '
        '"sandbox_exit_code": 0, '
        '"ci_detail": "no pipeline run appeared for c0ffee12 within 90s"}'
    ) as run_id:
        yield run_id


@pytest.fixture
async def ci_anchored_run() -> AsyncIterator[uuid.UUID]:
    """Graded by the project's own pipeline — the case invariant 3 actually asks for."""
    async with _run_at_gate(
        '{"exit_code": 0, "source": "ci", "independent_anchor": true, '
        '"url": "https://github.com/acme/payments/actions/runs/991", '
        '"sandbox_exit_code": 0, "ci_detail": "passed: CI"}'
    ) as run_id:
        yield run_id


async def test_the_evidence_names_who_ran_the_tests(
    accounts: dict[Role, str], suspended_run: uuid.UUID
) -> None:
    """A sandbox verdict is not an independent check, and the approver must be told so.

    Invariant 3 wants an external system of record. When the application has no pipeline, or
    it produced no verdict, the deviation has to be visible to the person relying on it — and
    so does the reason, because "we did not check" and "there was nothing to check" call for
    different responses from the approver.
    """
    async with _signed_in(accounts[Role.VIEWER]) as client:
        response = await client.get(f"/api/runs/{suspended_run}/evidence")
        assert response.status_code == 200, response.text
        document = response.json()["document"]

    assert document["tests"]["source"] == "sandbox"
    independence = [c for c in document["caveats"] if "not an independent check" in c]
    assert independence
    assert "no pipeline run appeared" in independence[0], "the caveat says why, not just that"
    assert any("No policy bundle was pinned" in caveat for caveat in document["caveats"])


async def test_a_ci_verdict_removes_the_independence_caveat(
    accounts: dict[Role, str], ci_anchored_run: uuid.UUID
) -> None:
    """The payoff of the CI adapter, asserted rather than assumed.

    A caveat that cannot be removed by fixing the underlying fact is decoration. This is the
    test that would fail if `_caveats` were ever simplified into always warning.
    """
    async with _signed_in(accounts[Role.VIEWER]) as client:
        document = (
            await client.get(f"/api/runs/{ci_anchored_run}/evidence")
        ).json()["document"]

    assert document["tests"]["source"] == "ci"
    assert document["tests"]["independent_anchor"] is True
    assert not any("not an independent check" in caveat for caveat in document["caveats"])
    # The other caveats are unaffected: this run still pinned no policy.
    assert any("No policy bundle was pinned" in caveat for caveat in document["caveats"])


async def test_a_decision_against_stale_evidence_is_refused(
    accounts: dict[Role, str], suspended_run: uuid.UUID
) -> None:
    """The digest is the entire control. If it does not bind, an approval means nothing."""
    async with _signed_in(accounts[Role.APPROVER]) as client:
        stale = (await client.get(f"/api/runs/{suspended_run}/evidence")).json()["digest"]

        # The run keeps moving while the page is open. This is ordinary, not exotic.
        async with connect() as conn:
            await conn.execute(
                "INSERT INTO flow_events (run_id, seq, kind, payload) "
                "VALUES ($1, 2, 'gate_reached', '{\"needed\": 2}'::jsonb)",
                suspended_run,
            )

        fresh = (await client.get(f"/api/runs/{suspended_run}/evidence")).json()["digest"]
        assert fresh != stale, "appending an event must change the digest"

        refused = await client.post(
            f"/api/runs/{suspended_run}/approval",
            json={"approved": True, "evidence_digest": stale, "comment": ""},
        )
        assert refused.status_code == 409
        assert "changed after this page was loaded" in refused.json()["detail"]


async def test_the_digest_does_not_depend_on_key_order(
    accounts: dict[Role, str], suspended_run: uuid.UUID
) -> None:
    """Assembling the same facts twice must produce the same digest.

    Otherwise every approval races the next page load, and the failure looks like approvals
    randomly bouncing rather than like a canonicalisation bug.
    """
    async with _signed_in(accounts[Role.VIEWER]) as client:
        first = (await client.get(f"/api/runs/{suspended_run}/evidence")).json()["digest"]
        second = (await client.get(f"/api/runs/{suspended_run}/evidence")).json()["digest"]
    assert first == second


async def test_a_viewer_cannot_decide(
    accounts: dict[Role, str], suspended_run: uuid.UUID
) -> None:
    async with _signed_in(accounts[Role.VIEWER]) as client:
        digest = (await client.get(f"/api/runs/{suspended_run}/evidence")).json()["digest"]
        refused = await client.post(
            f"/api/runs/{suspended_run}/approval",
            json={"approved": True, "evidence_digest": digest, "comment": ""},
        )
    assert refused.status_code == 403


# --- starting a run -------------------------------------------------------------------------


async def test_a_run_cannot_start_without_ticketing_configured(
    accounts: dict[Role, str],
) -> None:
    """There is no rule admitting the ticket, so there is nothing to start.

    Defaulting to "accept anything" here would mean the first real run was governed by an
    admission rule nobody wrote — the same failure as inferring `control_mode`.
    """
    async with _signed_in(accounts[Role.ADMIN]) as client:
        created = await client.post(
            "/api/applications",
            json={
                "name": f"payments-{uuid.uuid4().hex[:8]}",
                "scm_provider": "github",
                "org": "acme",
                "repo": "payments",
                "integration_model": "gated_deployment",
            },
        )
        app_id = created.json()["id"]

        refused = await client.post(
            f"/api/applications/{app_id}/runs", json={"ticket_id": "PAY-1234"}
        )
        assert refused.status_code == 409
        assert "no ticketing configured" in refused.json()["detail"]

        await client.delete(f"/api/applications/{app_id}")


async def test_a_viewer_cannot_start_a_run(accounts: dict[Role, str]) -> None:
    """Starting work is an operational act; watching it is not."""
    async with _signed_in(accounts[Role.VIEWER]) as client:
        refused = await client.post(
            f"/api/applications/{uuid.uuid4()}/runs", json={"ticket_id": "PAY-1234"}
        )
        assert refused.status_code == 403


async def test_starting_a_run_for_an_unknown_application_is_a_404(
    accounts: dict[Role, str],
) -> None:
    async with _signed_in(accounts[Role.APPROVER]) as client:
        missing = await client.post(
            f"/api/applications/{uuid.uuid4()}/runs", json={"ticket_id": "PAY-1234"}
        )
        assert missing.status_code == 404


# --- repository URLs, as an operator actually pastes them ---------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/KunLee/sasagayo",
        # What the platform's copy button gives you.
        "https://github.com/KunLee/sasagayo.git",
        "https://github.com/KunLee/sasagayo/",
    ],
)
def test_a_github_url_parses_the_same_however_it_was_copied(url: str) -> None:
    """`.git` left on would register `sasagayo.git`, and every call would 404.

    That failure reads like a bad token, so the operator regenerates a credential that was
    fine — which is a long way to travel for a suffix.
    """
    repo = _repo_config(url)
    assert repo.provider == "github"
    assert repo.org == "KunLee"
    assert repo.repo == "sasagayo"


def test_an_azure_repos_url_parses() -> None:
    repo = _repo_config("https://dev.azure.com/kunleeing0494/Sasagayo/_git/sasagayo")
    assert repo.provider == "azure_repos"
    assert repo.org == "kunleeing0494"
    assert repo.project == "Sasagayo"
    assert repo.repo == "sasagayo"


# --- the control point, after registration --------------------------------------------------


async def test_the_control_point_can_be_moved_and_the_move_is_recorded(
    accounts: dict[Role, str],
) -> None:
    """It used to be permanent — no update endpoint, and DELETE refused once a run existed.

    Changing it silently would be worse than leaving it stuck: `flow_runs` does not record
    which integration model governed a run, so a change re-interprets every past run under a
    control point that was not in force. The `app_changes` row is what makes that visible.
    """
    async with _signed_in(accounts[Role.ADMIN]) as client:
        created = await client.post(
            "/api/applications",
            json={
                "name": f"cp-{uuid.uuid4().hex[:8]}",
                "scm_provider": "github",
                "org": "acme",
                "repo": "payments",
                "integration_model": "kuwarden_deploys",
            },
        )
        assert created.status_code == 201, created.text
        app_id = created.json()["id"]

        moved = await client.patch(
            f"/api/applications/{app_id}/control-point",
            json={"integration_model": "gated_merge", "reason": "registered with the wrong one"},
        )
        assert moved.status_code == 200, moved.text
        assert moved.json()["changed"] is True
        assert moved.json()["runs_predating_this_change"] == 0

        listed = await client.get(f"/api/applications/{app_id}/changes")
        assert listed.status_code == 200
        entry = listed.json()[0]
        assert entry["field"] == "integration_model"
        assert entry["old_value"] == "kuwarden_deploys"
        assert entry["new_value"] == "gated_merge"
        # The authenticated principal, not anything the client supplied.
        assert entry["changed_by"] == accounts[Role.ADMIN]
        assert "wrong one" in entry["reason"]

        await client.delete(f"/api/applications/{app_id}")


async def test_setting_the_same_control_point_records_nothing(
    accounts: dict[Role, str],
) -> None:
    """A no-op entry is noise that buries the real ones."""
    async with _signed_in(accounts[Role.ADMIN]) as client:
        created = await client.post(
            "/api/applications",
            json={
                "name": f"cp-{uuid.uuid4().hex[:8]}",
                "scm_provider": "github",
                "org": "acme",
                "repo": "payments",
                "integration_model": "gated_merge",
            },
        )
        app_id = created.json()["id"]

        same = await client.patch(
            f"/api/applications/{app_id}/control-point",
            json={"integration_model": "gated_merge", "reason": "no change"},
        )
        assert same.json()["changed"] is False
        assert (await client.get(f"/api/applications/{app_id}/changes")).json() == []

        await client.delete(f"/api/applications/{app_id}")


async def test_a_change_reason_is_required(accounts: dict[Role, str]) -> None:
    """A change log full of blank reasons is a list of timestamps."""
    async with _signed_in(accounts[Role.ADMIN]) as client:
        created = await client.post(
            "/api/applications",
            json={
                "name": f"cp-{uuid.uuid4().hex[:8]}",
                "scm_provider": "github",
                "org": "acme",
                "repo": "payments",
                "integration_model": "gated_merge",
            },
        )
        app_id = created.json()["id"]

        refused = await client.patch(
            f"/api/applications/{app_id}/control-point",
            json={"integration_model": "kuwarden_deploys", "reason": ""},
        )
        assert refused.status_code == 422

        await client.delete(f"/api/applications/{app_id}")


async def test_a_viewer_cannot_move_the_control_point(accounts: dict[Role, str]) -> None:
    async with _signed_in(accounts[Role.VIEWER]) as client:
        refused = await client.patch(
            f"/api/applications/{uuid.uuid4()}/control-point",
            json={"integration_model": "gated_merge", "reason": "should not work"},
        )
        assert refused.status_code == 403


async def test_the_change_log_cannot_be_rewritten(accounts: dict[Role, str]) -> None:
    """Append-only, enforced by the database. A record an admin can edit records nothing."""
    async with _signed_in(accounts[Role.ADMIN]) as client:
        created = await client.post(
            "/api/applications",
            json={
                "name": f"cp-{uuid.uuid4().hex[:8]}",
                "scm_provider": "github",
                "org": "acme",
                "repo": "payments",
                "integration_model": "gated_merge",
            },
        )
        app_id = created.json()["id"]
        await client.patch(
            f"/api/applications/{app_id}/control-point",
            json={"integration_model": "kuwarden_deploys", "reason": "deliberate"},
        )

    async with connect() as conn:
        with pytest.raises(Exception, match="append-only"):
            await conn.execute(
                "UPDATE app_changes SET reason = 'tampered' WHERE app_id = $1",
                uuid.UUID(app_id),
            )
        with pytest.raises(Exception, match="append-only"):
            await conn.execute("DELETE FROM app_changes WHERE app_id = $1", uuid.UUID(app_id))
