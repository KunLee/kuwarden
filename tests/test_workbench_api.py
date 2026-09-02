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

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from fastapi.routing import APIRoute

from engine.api.auth import Role, create_user
from engine.api.main import _repo_config, app
from engine.config import ConfigError
from engine.db import connect
from tests.conftest import KUWARDEN_YAML

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
        "/api/applications/{app_id}/hooks/azure_devops": (
            "a service hook has no session to present. Authenticated by a shared secret "
            "compared with hmac.compare_digest, and refuses to run at all when "
            "KUWARDEN_WEBHOOK_SECRET is unset — see test_a_service_hook_without_a_secret_is_refused"
        ),
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


# --- webhook delivery records -------------------------------------------------------------


#: This repository's root, for the tests that read source rather than behaviour.
REPO_ROOT = Path(__file__).resolve().parents[1]


def test_every_hook_exit_records_the_delivery() -> None:
    """No exit from the hook may answer without leaving a row.

    Read from the source, because the property is "somebody adding a new admission rule cannot
    forget", and no runtime test covers a branch that does not exist yet. A delivery that
    returns without a record is the invisible case `trigger_deliveries` exists to end.
    """
    source = (REPO_ROOT / "engine" / "api" / "main.py").read_text(encoding="utf-8")
    hook = source[source.index("async def azure_devops_hook") :]
    hook = hook[: hook.index("\nasync def _delivered")]

    bare = [
        line.strip()
        for line in hook.splitlines()
        if line.strip().startswith('return {"started"')
    ]
    assert not bare, f"these exits bypass _delivered and leave no record: {bare}"


async def test_a_refused_delivery_is_still_recorded(
    hooked_app: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Silence is the failure mode. A refusal is a fact, and its reason ends an investigation.

    Before this, a webhook that arrived and was turned away looked identical to one that never
    arrived: nothing anywhere. Three separate mornings were spent in the Azure DevOps
    subscription history establishing which of the two had happened.
    """
    import engine.api.main as api

    monkeypatch.setattr("temporalio.client.Client.connect", _refuse_to_connect)

    headers = {"X-KuWarden-Token": "shared-secret-for-the-test"}
    async with _client() as client:
        # A qualifying state change, but without the tag the trigger demands.
        response = await client.post(
            _hook(hooked_app), json=_updated("Ready for Agent", tags="something-else"),
            headers=headers,
        )

    assert response.json()["started"] is False
    async with connect() as conn:
        row = await conn.fetchrow(
            "SELECT work_item, revision, started, reason FROM trigger_deliveries "
            "WHERE app_id = $1",
            hooked_app,
        )
    assert row is not None, "a refused delivery must still be recorded"
    assert row["started"] is False
    assert row["work_item"] == "29", "the record must name what was refused"
    assert "kuwarden-auto" in row["reason"]
    assert api  # imported for the module side effect the fixture relies on


async def test_supersession_holds_when_the_worker_never_wrote_a_run(
    hooked_app: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The hole this table was built to close, as it actually happened.

    The guard used to read `flow_runs`, whose rows are written by an *activity*. On the night
    work item 52 arrived as r2, r4 and r6, the worker could not execute a workflow task — so no
    row was ever written, the guard saw nothing, and all three replayed revisions were started.
    Three workflows sat in Temporal doing nothing, and the check meant to prevent exactly that
    was blind because it depended on the component that was broken.

    Here there is no `flow_runs` row at all, and the guard must still hold.
    """
    monkeypatch.setattr("temporalio.client.Client.connect", _refuse_to_connect)

    async with connect() as conn:
        await conn.execute(
            "INSERT INTO trigger_deliveries "
            "(app_id, provider, work_item, revision, started, reason) "
            "VALUES ($1,'azure_devops','29',6,true,null)",
            hooked_app,
        )

    headers = {"X-KuWarden-Token": "shared-secret-for-the-test"}
    async with _client() as client:
        replayed = await client.post(
            _hook(hooked_app), json=_updated("Ready for Agent", rev=2), headers=headers
        )

    assert replayed.json()["started"] is False
    assert "r6" in replayed.json()["reason"]

    async with connect() as conn:
        runs = await conn.fetchval(
            "SELECT count(*) FROM flow_runs WHERE app_id = $1", hooked_app
        )
    assert runs == 0, "no run existed, and none should have been started"


async def _refuse_to_connect(*_a: object, **_k: object) -> object:
    """Temporal must not be reached by these tests.

    Every case here is refused before the endpoint dials it. Making the connection itself fail
    is the assertion: if an admission rule ever stops short-circuiting, these tests break rather
    than quietly starting a workflow against a real Temporal.
    """
    raise AssertionError("the endpoint must refuse before reaching Temporal")


# --- the evidence graph (ADR 0012) --------------------------------------------------------


@asynccontextmanager
async def _ticket_with_two_runs() -> AsyncIterator[
    tuple[uuid.UUID, uuid.UUID, uuid.UUID, str, str]
]:
    """Ticket 50's shape, reduced: two runs for one ticket, both editing the same file.

    This is the collision the graph exists to make visible. Four real runs branched from one
    base, all edited `components/Header.tsx`, none could see the others, and two were merged —
    the second by hand into a state nothing had verified.
    """
    app_id, first, second = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    # Paths namespaced to this fixture. `run_files` is keyed on a bare path and the table is
    # global, so a test asserting "these are the runs that touched it" against a real path
    # passes only while nothing real has touched it — which stopped being true the moment the
    # development database was backfilled. Isolation by construction, not by an empty table.
    shared = f"components/Header-{app_id.hex[:8]}.tsx"
    alone = f"components/UserMenu-{app_id.hex[:8]}.tsx"
    async with connect() as conn:
        await conn.execute(
            "INSERT INTO app_registry (id, name, repo_url, integration_model) "
            "VALUES ($1,$2,$3,'gated_merge')",
            app_id, f"graph-{app_id.hex[:8]}", "https://example.invalid/graph",
        )
        for run_id, status in ((first, "succeeded"), (second, "suspended")):
            await conn.execute(
                "INSERT INTO flow_runs (id, root_run_id, app_id, workflow_id, ticket_system, "
                "ticket_id, risk_tier, status, schema_version, policy_commit, policy_bundle) "
                "VALUES ($1,$1,$2,$3,'azure_devops','50','medium',$4,1,$5,'{}'::jsonb)",
                run_id, app_id, f"kuwarden-ado-{app_id}-50-r{6 if run_id == first else 4}",
                status, "0" * 40,
            )
        await conn.executemany(
            "INSERT INTO run_files (run_id, path, added, removed, attempt) "
            "VALUES ($1,$2,$3,$4,$5)",
            [
                (first, shared, 40, 106, 1),
                (first, alone, 80, 0, 1),
                (second, shared, 39, 0, 0),
            ],
        )
    try:
        yield app_id, first, second, shared, alone
    finally:
        async with connect() as conn:
            await conn.execute(
                "DELETE FROM run_files WHERE run_id = ANY($1::uuid[])", [first, second]
            )
            await conn.execute("DELETE FROM flow_runs WHERE app_id = $1", app_id)
            await conn.execute("DELETE FROM app_registry WHERE id = $1", app_id)


async def test_the_graph_shows_every_run_for_one_ticket(
    accounts: dict[Role, str]
) -> None:
    """Asking about one run answers about the ticket, which is the question that was missing.

    Ticket 50 produced four runs from one base. Each was individually green and nothing in the
    product could show them beside each other, so the collision was found by a broken
    production deployment rather than by the record that contained every fact about it.
    """
    async with (
        _ticket_with_two_runs() as (_app, first, second, shared, _alone),
        _signed_in(accounts[Role.VIEWER]) as client,
    ):
        response = await client.get(f"/api/runs/{first}/graph")
        assert response.status_code == 200, response.text
        graph = response.json()

    runs = {n["label"]: n for n in graph["nodes"] if n["kind"] == "run"}
    assert set(runs) == {str(first)[:8], str(second)[:8]}, "the sibling run must be on the graph"
    assert runs[str(first)[:8]]["self"] is True
    # The revision each was launched for. Two runs at different revisions for one work item is
    # the replayed-backlog failure, and the graph should be able to show it.
    assert {r["revision"] for r in runs.values()} == {"6", "4"}

    # And the edge that matters: one file, two runs.
    collision = [e for e in graph["edges"] if e["to"] == f"file:{shared}"]
    assert len(collision) == 2, "both runs changed one file, and both edges must be present"


async def test_a_file_names_the_runs_that_changed_it(accounts: dict[Role, str]) -> None:
    """The reverse question — the one that had no answer at all before ADR 0012."""
    async with (
        _ticket_with_two_runs() as (_app, first, second, shared, alone),
        _signed_in(accounts[Role.VIEWER]) as client,
    ):
        touched = (await client.get(f"/api/files/{shared}/runs")).json()
        untouched = (await client.get(f"/api/files/{alone}/runs")).json()

    assert {row["run_id"] for row in touched} == {str(first), str(second)}
    assert all(row["ticket_id"] == "50" for row in touched)
    # Not every file is shared, and a query that returned everything would be useless.
    assert {row["run_id"] for row in untouched} == {str(first)}


async def test_a_later_push_replaces_the_earlier_count_for_the_same_file() -> None:
    """The index holds what is on the branch, not what the first attempt proposed.

    A run pushes more than once. `ON CONFLICT DO NOTHING` would freeze attempt 0's line counts
    and go on describing a change that attempt 1 had already superseded — the index would be
    quietly wrong about the only version that exists.
    """
    from engine.activities.audit import ChangedFile, RunFiles, record_run_files

    app_id, run_id = uuid.uuid4(), uuid.uuid4()
    async with connect() as conn:
        await conn.execute(
            "INSERT INTO app_registry (id, name, repo_url, integration_model) "
            "VALUES ($1,$2,$3,'gated_merge')",
            app_id, f"upsert-{app_id.hex[:8]}", "https://example.invalid/upsert",
        )
        await conn.execute(
            "INSERT INTO flow_runs (id, root_run_id, app_id, workflow_id, ticket_system, "
            "ticket_id, risk_tier, status, schema_version, policy_commit, policy_bundle) "
            "VALUES ($1,$1,$2,$3,'jira','PAY-9','low','running',1,$4,'{}'::jsonb)",
            run_id, app_id, f"kuwarden-{run_id}", "0" * 40,
        )
    try:
        await record_run_files(
            RunFiles(run_id=run_id, attempt=0,
                     files=[ChangedFile(path="app/page.tsx", added=200, removed=0)])
        )
        await record_run_files(
            RunFiles(run_id=run_id, attempt=1,
                     files=[ChangedFile(path="app/page.tsx", added=12, removed=3)])
        )
        # An empty diff is a deduplicated push, not a reverted run: it must not clear the row.
        await record_run_files(RunFiles(run_id=run_id, attempt=2, files=[]))

        async with connect() as conn:
            rows = await conn.fetch(
                "SELECT path, added, removed, attempt FROM run_files WHERE run_id = $1", run_id
            )
    finally:
        async with connect() as conn:
            await conn.execute("DELETE FROM run_files WHERE run_id = $1", run_id)
            await conn.execute("DELETE FROM flow_runs WHERE id = $1", run_id)
            await conn.execute("DELETE FROM app_registry WHERE id = $1", app_id)

    assert len(rows) == 1, "one row per (run, path), not one per attempt"
    assert (rows[0]["added"], rows[0]["removed"], rows[0]["attempt"]) == (12, 3, 1)


# --- the approval gate ----------------------------------------------------------------------


@asynccontextmanager
async def _run_at_gate(
    verdict: str, *, extra: list[tuple[str, str | None, str]] | None = None
) -> AsyncIterator[uuid.UUID]:
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
        for seq, (kind, node_id, payload) in enumerate(extra or [], start=2):
            await conn.execute(
                "INSERT INTO flow_events (run_id, seq, kind, node_id, payload) "
                "VALUES ($1, $2, $3, $4, $5::jsonb)",
                run_id, seq, kind, node_id, payload,
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


#: Ticket 50, trimmed to the shape that matters: a verifier that passed while writing down
#: the reason the change did not work, and the only one that objected turned advisory.
TICKET_50_VERIFIERS = (
    '{"passed": 3, "of": 4, "falsified_by": ["test_evidence"], "verifications": ['
    '{"verifier": "correctness", "blocks": false, "findings": ['
    '"the ticket asked for the profile control itself to offer the admin jump, '
    'not to keep the old separate icon as well"]},'
    '{"verifier": "security", "blocks": false, "findings": []},'
    '{"verifier": "test_evidence", "blocks": true, "findings": ['
    '"zero test line changes for 87 lines of new source behavior"]}]}'
)


async def test_the_approver_is_shown_what_the_verifiers_wrote(
    accounts: dict[Role, str]
) -> None:
    """The failure this was written for, and the most expensive one so far.

    On ticket 50 the gate showed `3 of 4 passed` and nothing else. It was true. The change was
    approved in 157 seconds, merged, deployed — and did not implement the feature. `correctness`
    had returned a *passing* verdict while writing, in its findings, exactly what was missing;
    `test_evidence` had blocked and was configured advisory, so it was overridden.

    Every fact needed to refuse that change was in the audit trail and none of it was on the
    page. A verdict is a judgement; the findings are what it was a judgement about, and only
    the first was reaching the person deciding.
    """
    async with _run_at_gate(
        '{"exit_code": 0, "source": "ci", "independent_anchor": true, "ci_detail": "passed: CI"}',
        extra=[
            ("verifiers_completed", None, TICKET_50_VERIFIERS),
            (
                "verifier_overridden",
                None,
                '{"advisory": ["test_evidence"], "detail": "declared advisory"}',
            ),
        ],
    ) as run_id, _signed_in(accounts[Role.VIEWER]) as client:
        response = await client.get(f"/api/runs/{run_id}/evidence")
        assert response.status_code == 200, response.text
        document = response.json()["document"]

    # The words themselves, not a count.
    written = [f for v in document["verifications"] for f in v["findings"]]
    assert any("not to keep the old separate icon as well" in f for f in written), (
        "the finding that describes the feature not being implemented must reach the page"
    )

    caveats = " ".join(document["caveats"])
    # A verifier that refused the change and could not stop it is the strongest fact here.
    assert "test_evidence falsified this change" in caveats
    assert "advisory" in caveats
    # And the findings of the verifiers that passed are not silently the same as none.
    assert "did not stop this change" in caveats


async def test_a_clean_run_carries_no_findings_caveat(
    accounts: dict[Role, str], ci_anchored_run: uuid.UUID
) -> None:
    """The control. A caveat that is always present is decoration, not a warning."""
    async with _signed_in(accounts[Role.VIEWER]) as client:
        document = (
            await client.get(f"/api/runs/{ci_anchored_run}/evidence")
        ).json()["document"]

    assert document["verifications"] == []
    assert not any("did not stop this change" in caveat for caveat in document["caveats"])


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


def test_a_url_without_a_scheme_parses_the_same() -> None:
    """Operators paste what is in front of them, and the address bar hides the scheme."""
    assert _repo_config("github.com/KunLee/sasagayo").org == "KunLee"


@pytest.mark.parametrize(
    "url",
    [
        "https://example.invalid/x",
        "https://github.com",
        "https://github.com/KunLee",
        "https://dev.azure.com/kunleeing0494",
        # Parses into a plausible RepoConfig from the wrong segments unless `_git` is required
        # — org would become "dev.azure.com", and the 404 that follows names a repository the
        # operator never typed.
        "https://dev.azure.com/kunleeing0494/Sasagayo",
    ],
)
def test_a_url_that_is_not_a_repository_says_so(url: str) -> None:
    """It used to be an `IndexError` off the end of the segment list.

    That reaches the operator as a 500 with no body — the least actionable failure this
    endpoint can produce, for the most obvious kind of mistake. `ConfigError` is caught by both
    callers, so it now surfaces as a named check failure instead.
    """
    with pytest.raises(ConfigError) as raised:
        _repo_config(url)
    # The message names the shape that was wanted. "Invalid URL" tells nobody what to type.
    assert "expected" in str(raised.value)
    assert url in str(raised.value)


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


# --- the Azure DevOps service hook ---------------------------------------------------------
#
# The endpoint is reachable without a session, so these are the only thing standing between
# the internet and something that spends model budget and writes code.


def _updated(state: str | None, tags: str = "kuwarden-auto", rev: int = 3) -> dict[str, object]:
    """A `workitem.updated` payload. `state=None` is a save that changed something else."""
    fields: dict[str, object] = {"System.AreaPath": {"oldValue": "A", "newValue": "B"}}
    if state is not None:
        fields = {"System.State": {"oldValue": "New", "newValue": state}}
    return {
        "eventType": "workitem.updated",
        "resource": {
            "workItemId": 29,
            "rev": rev,
            "fields": fields,
            "revision": {"fields": {"System.Tags": tags}},
        },
    }


@pytest.fixture
async def hooked_app(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[uuid.UUID]:
    """An application with an azure_devops trigger that declares a ready state."""
    monkeypatch.setenv("KUWARDEN_WEBHOOK_SECRET", "shared-secret-for-the-test")
    app_id = uuid.uuid4()
    async with connect() as conn:
        await conn.execute(
            "INSERT INTO app_registry (id, name, repo_url, integration_model) "
            "VALUES ($1,$2,$3,'gated_merge')",
            app_id, f"hook-{app_id.hex[:8]}", "https://example.invalid/hooked",
        )
        await conn.execute(
            "INSERT INTO app_triggers (id, app_id, provider, project, organisation, label, "
            "ready_state) VALUES ($1,$2,'azure_devops','Sasagayo','org','kuwarden-auto',"
            "'Ready for Agent')",
            uuid.uuid4(), app_id,
        )
    try:
        yield app_id
    finally:
        async with connect() as conn:
            await conn.execute("DELETE FROM app_triggers WHERE app_id = $1", app_id)
            await conn.execute("DELETE FROM app_registry WHERE id = $1", app_id)


def _hook(app_id: uuid.UUID) -> str:
    return f"/api/applications/{app_id}/hooks/azure_devops"


async def test_a_service_hook_without_a_secret_is_refused(
    hooked_app: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail closed. An unset secret must not mean "accept anyone"."""
    monkeypatch.delenv("KUWARDEN_WEBHOOK_SECRET", raising=False)
    async with _client() as client:
        response = await client.post(_hook(hooked_app), json=_updated("Ready for Agent"))
    assert response.status_code == 503


async def test_a_service_hook_with_the_wrong_token_is_refused(hooked_app: uuid.UUID) -> None:
    async with _client() as client:
        response = await client.post(
            _hook(hooked_app),
            json=_updated("Ready for Agent"),
            headers={"X-KuWarden-Token": "not-the-secret"},
        )
    assert response.status_code == 401


async def test_a_save_that_did_not_change_the_state_starts_nothing(
    hooked_app: uuid.UUID,
) -> None:
    """The distinction the whole design rests on — migration 006.

    Azure DevOps fires `workitem.updated` for a reassignment or a typo fix. Only the fields
    that actually changed appear in `resource.fields`, so a save that left the state alone
    carries no `System.State` at all.
    """
    async with _client() as client:
        response = await client.post(
            _hook(hooked_app),
            json=_updated(None),
            headers={"X-KuWarden-Token": "shared-secret-for-the-test"},
        )
    assert response.status_code == 200
    assert response.json()["started"] is False


async def test_a_move_into_another_state_starts_nothing(hooked_app: uuid.UUID) -> None:
    async with _client() as client:
        response = await client.post(
            _hook(hooked_app),
            json=_updated("Active"),
            headers={"X-KuWarden-Token": "shared-secret-for-the-test"},
        )
    assert response.json()["started"] is False


async def test_the_ready_state_without_the_tag_starts_nothing(hooked_app: uuid.UUID) -> None:
    async with _client() as client:
        response = await client.post(
            _hook(hooked_app),
            json=_updated("Ready for Agent", tags="bug; needs-triage"),
            headers={"X-KuWarden-Token": "shared-secret-for-the-test"},
        )
    assert response.json()["started"] is False


async def test_a_trigger_with_no_ready_state_refuses_to_fire(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without a transition to test, every state change qualifies — the rejected design."""
    monkeypatch.setenv("KUWARDEN_WEBHOOK_SECRET", "shared-secret-for-the-test")
    app_id = uuid.uuid4()
    async with connect() as conn:
        await conn.execute(
            "INSERT INTO app_registry (id, name, repo_url, integration_model) "
            "VALUES ($1,$2,$3,'gated_merge')",
            app_id, f"hook-{app_id.hex[:8]}", "https://example.invalid/nostate",
        )
        await conn.execute(
            "INSERT INTO app_triggers (id, app_id, provider, project, organisation, label) "
            "VALUES ($1,$2,'azure_devops','Sasagayo','org','kuwarden-auto')",
            uuid.uuid4(), app_id,
        )
    try:
        async with _client() as client:
            response = await client.post(
                _hook(app_id),
                json=_updated("Ready for Agent"),
                headers={"X-KuWarden-Token": "shared-secret-for-the-test"},
            )
        assert response.status_code == 409
        assert "ready_state" in response.json()["detail"]
    finally:
        async with connect() as conn:
            await conn.execute("DELETE FROM app_triggers WHERE app_id = $1", app_id)
            await conn.execute("DELETE FROM app_registry WHERE id = $1", app_id)


async def test_a_qualifying_transition_starts_exactly_one_run(
    hooked_app: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """And a redelivery of the same revision starts none.

    `_launch` is replaced rather than reaching Temporal: what is under test is the decision to
    start and the id it derives, not Temporal's ability to run a workflow. The second call
    raises what Temporal raises for a duplicate id, which is the behaviour being relied on.
    """
    from temporalio.exceptions import WorkflowAlreadyStartedError

    import engine.api.main as api

    launched: list[str] = []

    async def fake_launch(
        _client: object,
        _app: uuid.UUID,
        ticket: str,
        provider: str,
        workflow_id: str,
        *,
        app_name: str = "",
        reject_duplicate: bool = False,
    ) -> tuple[uuid.UUID, str]:
        # The hook must ask for REJECT_DUPLICATE. Temporal's default only refuses an id while
        # the previous run is open, so without this a redelivery arriving after the run
        # finished starts a second one — and a webhook retry is exactly that case.
        assert reject_duplicate is True, "the hook path must reject duplicate workflow ids"
        # Carried so Triage can refuse a run whose application does not match the
        # configuration the worker resolved for it.
        assert app_name, "the hook path must name the application the run is for"
        if workflow_id in launched:
            raise WorkflowAlreadyStartedError(workflow_id, "DeliveryFlow")
        launched.append(workflow_id)
        assert (ticket, provider) == ("29", "azure_devops")
        return uuid.uuid4(), workflow_id

    async def fake_connect(*_a: object, **_k: object) -> object:
        return object()

    monkeypatch.setattr(api, "_launch", fake_launch)
    monkeypatch.setattr("temporalio.client.Client.connect", fake_connect)

    headers = {"X-KuWarden-Token": "shared-secret-for-the-test"}
    async with _client() as client:
        url, body = _hook(hooked_app), _updated("Ready for Agent")
        first = await client.post(url, json=body, headers=headers)
        second = await client.post(url, json=body, headers=headers)

    assert first.json()["started"] is True
    # The id is derived from the work item and the revision that moved it, so the redelivery
    # collides deliberately.
    assert second.json()["started"] is False
    assert len(launched) == 1


async def test_a_replayed_older_revision_does_not_start_a_second_run(
    hooked_app: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The failure this was written for, from a real morning.

    Azure DevOps replays its backlog when a subscription is recreated, and it does not replay
    in order: work item 50 arrived as r6, then r2, then r4. Rejecting a *duplicate* revision
    cannot see that a revision is *older* than one already run, so r2 and r4 each started a
    whole pipeline against an intent r6 had already superseded — three runs from one base,
    unaware of each other, two of them ending in a pull request for a ticket state that no
    longer existed.

    Both halves are asserted, because a guard that refuses everything would pass the first.
    """
    import engine.api.main as api

    launched: list[str] = []

    async def fake_launch(
        _client: object,
        _app: uuid.UUID,
        _ticket: str,
        _provider: str,
        workflow_id: str,
        *,
        app_name: str = "",
        reject_duplicate: bool = False,
    ) -> tuple[uuid.UUID, str]:
        launched.append(workflow_id)
        return uuid.uuid4(), workflow_id

    async def fake_connect(*_a: object, **_k: object) -> object:
        return object()

    monkeypatch.setattr(api, "_launch", fake_launch)
    monkeypatch.setattr("temporalio.client.Client.connect", fake_connect)

    # r6 has already run. Written directly rather than by starting one: what is under test is
    # what the *next* delivery does about a run that exists.
    already = uuid.uuid4()
    async with connect() as conn:
        await conn.execute(
            """
            INSERT INTO flow_runs (id, root_run_id, app_id, workflow_id, ticket_system,
                                   ticket_id, risk_tier, status, schema_version,
                                   policy_commit, policy_bundle)
            VALUES ($1,$1,$2,$3,'azure_devops','29','low','succeeded',1,$4,'{}')
            """,
            already, hooked_app, api._hook_workflow_id(hooked_app, 29, 6), "0" * 40,
        )

    headers = {"X-KuWarden-Token": "shared-secret-for-the-test"}
    try:
        async with _client() as client:
            url = _hook(hooked_app)
            replayed = await client.post(
                url, json=_updated("Ready for Agent", rev=2), headers=headers
            )
            newer = await client.post(
                url, json=_updated("Ready for Agent", rev=7), headers=headers
            )
    finally:
        async with connect() as conn:
            await conn.execute("DELETE FROM flow_runs WHERE id = $1", already)

    assert replayed.json()["started"] is False
    assert "r6" in replayed.json()["reason"], "the record must name what superseded it"
    # And the guard must not wedge the trigger: a genuinely newer revision still runs.
    assert newer.json()["started"] is True
    assert launched == [api._hook_workflow_id(hooked_app, 29, 7)]


#: Azure DevOps' own sample payload, as delivered by the subscription dialog's Test button.
#: Trimmed to the fields this endpoint reads, and otherwise verbatim — including the three
#: different id numbers, which is the point of keeping it.
ADO_TEST_BUTTON_PAYLOAD: dict[str, object] = {
    "eventType": "workitem.updated",
    "publisherId": "tfs",
    "resource": {
        # The update record's id. NOT the work item.
        "id": 2,
        # Zero in the sample, and zero is falsy — the reason this payload is worth keeping.
        "workItemId": 0,
        "rev": 2,
        "fields": {
            "System.Rev": {"oldValue": "1", "newValue": "2"},
            "System.State": {"oldValue": "New", "newValue": "Approved"},
            "System.Reason": {
                "oldValue": "New defect reported",
                "newValue": "Approved by the Product Owner",
            },
        },
        "revision": {
            # The work item.
            "id": 5,
            "rev": 2,
            "fields": {
                "System.TeamProject": "FabrikamCloud",
                "System.State": "New",
                "System.Title": "Some great new idea!",
            },
        },
    },
}


async def test_the_test_button_payload_is_handled_and_admits_nothing(
    hooked_app: uuid.UUID,
) -> None:
    """Azure DevOps' sample moves to 'Approved', so a correct receiver declines it.

    Worth asserting because the Test button is how an operator checks their subscription, and
    "started: false" there is success rather than the failure it looks like.
    """
    async with _client() as client:
        response = await client.post(
            _hook(hooked_app),
            json=ADO_TEST_BUTTON_PAYLOAD,
            headers={"X-KuWarden-Token": "shared-secret-for-the-test"},
        )
    assert response.status_code == 200
    assert response.json()["started"] is False
    assert "Approved" in response.json()["reason"]


async def test_the_work_item_is_never_taken_from_the_update_record_id(
    hooked_app: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`resource.id`, `resource.workItemId` and `revision.id` are three different numbers.

    With `workItemId` falsy, the work item must come from `revision.id` (5) and never from
    `resource.id` (2) — which identifies the update, not the ticket. Reading the wrong one
    starts a real run against a ticket nobody asked about, and every other assertion in this
    file would still pass.
    """
    import engine.api.main as api

    seen: list[str] = []

    async def fake_launch(
        _client: object,
        _app: uuid.UUID,
        ticket: str,
        _provider: str,
        workflow_id: str,
        **_kw: object,
    ) -> tuple[uuid.UUID, str]:
        seen.append(ticket)
        return uuid.uuid4(), workflow_id

    async def fake_connect(*_a: object, **_k: object) -> object:
        return object()

    monkeypatch.setattr(api, "_launch", fake_launch)
    monkeypatch.setattr("temporalio.client.Client.connect", fake_connect)

    # The sample payload, moved into the state this application admits.
    payload = json.loads(json.dumps(ADO_TEST_BUTTON_PAYLOAD))
    payload["resource"]["fields"]["System.State"]["newValue"] = "Ready for Agent"
    payload["resource"]["revision"]["fields"]["System.Tags"] = "kuwarden-auto"

    async with _client() as client:
        response = await client.post(
            _hook(hooked_app),
            json=payload,
            headers={"X-KuWarden-Token": "shared-secret-for-the-test"},
        )

    assert response.json()["started"] is True
    assert seen == ["5"], "the work item is revision.id, not resource.id"


# --- amending a trigger ----------------------------------------------------------------------


async def test_a_trigger_can_be_amended_without_being_recreated(
    hooked_app: uuid.UUID, accounts: dict[Role, str]
) -> None:
    """The whole point: no window in which the application accepts no work.

    Delete-and-recreate leaves `POST /runs` refusing and a service hook 404ing for as long as
    the gap lasts, to change one field.
    """
    async with connect() as conn:
        trigger_id = await conn.fetchval(
            "SELECT id FROM app_triggers WHERE app_id = $1", hooked_app
        )

    async with _signed_in(accounts[Role.ADMIN]) as client:
        response = await client.patch(
            f"/api/applications/{hooked_app}/triggers/{trigger_id}",
            json={"ready_state": "Approved", "max_story_points": 8},
        )

    assert response.status_code == 200
    assert response.json()["ready_state"] == "Approved"

    async with connect() as conn:
        row = await conn.fetchrow(
            "SELECT label, ready_state, max_story_points FROM app_triggers WHERE id = $1",
            trigger_id,
        )
    assert row["ready_state"] == "Approved"
    assert row["max_story_points"] == 8
    # Untouched, because it was not in the body.
    assert row["label"] == "kuwarden-auto"


async def test_an_omitted_field_is_left_alone_and_an_explicit_null_clears_it(
    hooked_app: uuid.UUID, accounts: dict[Role, str]
) -> None:
    """`null` and absent must not mean the same thing.

    If they did there would be no way to stop requiring a ready state, and unsetting one is
    exactly the amendment somebody eventually needs.
    """
    async with connect() as conn:
        trigger_id = await conn.fetchval(
            "SELECT id FROM app_triggers WHERE app_id = $1", hooked_app
        )

    async with _signed_in(accounts[Role.ADMIN]) as client:
        cleared = await client.patch(
            f"/api/applications/{hooked_app}/triggers/{trigger_id}",
            json={"ready_state": None},
        )
    assert cleared.status_code == 200

    async with connect() as conn:
        row = await conn.fetchrow(
            "SELECT label, ready_state FROM app_triggers WHERE id = $1", trigger_id
        )
    assert row["ready_state"] is None
    assert row["label"] == "kuwarden-auto", "an omitted field must not be cleared"


async def test_identity_fields_are_not_amendable(
    hooked_app: uuid.UUID, accounts: dict[Role, str]
) -> None:
    """Provider, organisation and project decide *which* board a rule governs.

    Amending them in place would silently re-point an existing rule at a different board, so
    the model forbids extra keys — the request is refused by name rather than answered with a
    200 that changed nothing, which is what dropping unknown keys would produce.
    """
    async with connect() as conn:
        trigger_id = await conn.fetchval(
            "SELECT id FROM app_triggers WHERE app_id = $1", hooked_app
        )

    async with _signed_in(accounts[Role.ADMIN]) as client:
        response = await client.patch(
            f"/api/applications/{hooked_app}/triggers/{trigger_id}",
            json={"project": "SomeOtherProject"},
        )

    assert response.status_code == 422
    async with connect() as conn:
        project = await conn.fetchval(
            "SELECT project FROM app_triggers WHERE id = $1", trigger_id
        )
    assert project == "Sasagayo"


async def test_a_viewer_cannot_amend_a_trigger(
    hooked_app: uuid.UUID, accounts: dict[Role, str]
) -> None:
    async with connect() as conn:
        trigger_id = await conn.fetchval(
            "SELECT id FROM app_triggers WHERE app_id = $1", hooked_app
        )
    async with _signed_in(accounts[Role.VIEWER]) as client:
        response = await client.patch(
            f"/api/applications/{hooked_app}/triggers/{trigger_id}",
            json={"ready_state": "Anything"},
        )
    assert response.status_code == 403


async def test_diagnostics_uses_the_stored_workflow_id_not_a_rebuilt_one(
    accounts: dict[Role, str]
) -> None:
    """A hook-started run is keyed on the work item, not on the run id.

    `kuwarden-{run_id}` is the manual path's convention only. Rebuilding it here looked up a
    workflow that never existed and told the operator their history was missing, for a run
    whose history was fine — so the id must come from the row that recorded it.
    """
    app_id, run_id = uuid.uuid4(), uuid.uuid4()
    stored = f"kuwarden-ado-{app_id}-29-r14"
    asked: list[str] = []

    class FakeHandle:
        async def fetch_history_events(self) -> AsyncIterator[object]:
            # An async generator that raises on first use: what Temporal does for an id it
            # does not know. The endpoint turns it into the "history unavailable" 404, which
            # is the path the bug showed up on.
            for _ in ():
                yield _
            raise RuntimeError("workflow not found")

    class FakeClient:
        def get_workflow_handle(self, workflow_id: str) -> FakeHandle:
            asked.append(workflow_id)
            return FakeHandle()

    async def fake_connect(*_a: object, **_k: object) -> FakeClient:
        return FakeClient()

    async with connect() as conn:
        await conn.execute(
            "INSERT INTO app_registry (id, name, repo_url, integration_model) "
            "VALUES ($1,$2,$3,'gated_merge')",
            app_id, f"diag-{app_id.hex[:8]}", "https://example.invalid/diag",
        )
        await conn.execute(
            "INSERT INTO flow_runs (id, root_run_id, app_id, workflow_id, ticket_system, "
            "ticket_id, risk_tier, status, schema_version, policy_commit, policy_bundle) "
            "VALUES ($1,$1,$2,$3,'azure_devops','29','low','rejected',1,'unpinned:test','{}')",
            run_id, app_id, stored,
        )
    try:
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("temporalio.client.Client.connect", fake_connect)
            async with _signed_in(accounts[Role.VIEWER]) as client:
                await client.get(f"/api/runs/{run_id}/diagnostics")
        assert asked == [stored], f"asked Temporal for {asked}, expected the stored id"
    finally:
        async with connect() as conn:
            await conn.execute("DELETE FROM flow_runs WHERE id = $1", run_id)
            await conn.execute("DELETE FROM app_registry WHERE id = $1", app_id)


# --- arming and disarming verifiers ---------------------------------------------------------


async def test_disarming_a_verifier_makes_it_advisory_not_absent(
    hooked_app: uuid.UUID, accounts: dict[Role, str]
) -> None:
    """Off must not mean skipped.

    A skipped verifier saves a model call and destroys the evidence. An advisory one still
    runs, still records findings, and still reaches the trail — it simply cannot abort. For a
    product whose value is the record, only one of those is an acceptable meaning for "off".
    """
    async with connect() as conn:
        await conn.execute(
            "INSERT INTO app_config (app_id, yaml, updated_by) VALUES ($1,$2,'test')",
            hooked_app, KUWARDEN_YAML,
        )

    async with _signed_in(accounts[Role.ADMIN]) as client:
        response = await client.put(
            f"/api/applications/{hooked_app}/verifiers",
            json={"blocking": {"test_evidence": False}},
        )
    assert response.status_code == 200
    assert response.json()["advisory"] == ["test_evidence"]

    # The other three are untouched — a partial request must not disarm what it did not name.
    blocking = {v["name"]: v["blocking"] for v in response.json()["verifiers"]}
    assert blocking == {
        "correctness": True,
        "security": True,
        "test_evidence": False,
        "regression_risk": True,
    }


async def test_re_arming_does_not_duplicate_the_block(
    hooked_app: uuid.UUID, accounts: dict[Role, str]
) -> None:
    """The rewrite is textual, so it has to be idempotent or the file grows a block per click."""
    async with connect() as conn:
        await conn.execute(
            "INSERT INTO app_config (app_id, yaml, updated_by) VALUES ($1,$2,'test')",
            hooked_app, KUWARDEN_YAML,
        )

    async with _signed_in(accounts[Role.ADMIN]) as client:
        await client.put(
            f"/api/applications/{hooked_app}/verifiers",
            json={"blocking": {"test_evidence": False}},
        )
        again = await client.put(
            f"/api/applications/{hooked_app}/verifiers",
            json={"blocking": {"test_evidence": True}},
        )
    assert again.json()["advisory"] == []

    async with connect() as conn:
        stored = str(await conn.fetchval("SELECT yaml FROM app_config WHERE app_id=$1", hooked_app))
    assert stored.count("verification:") == 1
    # The comments in a kuwarden.yaml carry the reasoning for its settings, which is most of
    # what makes the file reviewable. A YAML round-trip would have discarded every one.
    assert stored.count("#") >= KUWARDEN_YAML.count("#")


async def test_an_unknown_verifier_is_refused(
    hooked_app: uuid.UUID, accounts: dict[Role, str]
) -> None:
    """A typo would otherwise disable nothing while reading as though it had."""
    async with _signed_in(accounts[Role.ADMIN]) as client:
        response = await client.put(
            f"/api/applications/{hooked_app}/verifiers",
            json={"blocking": {"test_evidince": False}},
        )
    assert response.status_code == 422
    assert "test_evidince" in response.json()["detail"]


async def test_a_viewer_cannot_disarm_a_verifier(
    hooked_app: uuid.UUID, accounts: dict[Role, str]
) -> None:
    async with _signed_in(accounts[Role.VIEWER]) as client:
        response = await client.put(
            f"/api/applications/{hooked_app}/verifiers",
            json={"blocking": {"test_evidence": False}},
        )
    assert response.status_code == 403


# --- stopping a run -------------------------------------------------------------------------


async def test_terminating_a_run_records_who_did_it_and_what_was_left_behind(
    accounts: dict[Role, str], suspended_run: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run stopped by a person gets its own outcome, not `aborted`.

    Two facts have to survive, and neither is recoverable afterwards. **Who** stopped it —
    the flow did not decide this, a named human did, and `aborted` would say the opposite.
    And **what is still on the remote** — terminating skips compensation, so the branch this
    run pushed is still there, and an operator who finds that out a week later has already
    lost the chance to decide about it.
    """
    async with connect() as conn:
        await conn.execute(
            "INSERT INTO flow_events (run_id, seq, kind, node_id, payload) "
            "VALUES ($1, 2, 'branch_pushed', 'push', $2::jsonb)",
            suspended_run,
            '{"branch": "kuwarden/pay-1234-abcd1234", "commit": "c0ffee12"}',
        )

    terminated: list[str] = []

    class _Handle:
        async def terminate(self, reason: str = "") -> None:
            terminated.append(reason)

    class _Client:
        def get_workflow_handle(self, workflow_id: str) -> _Handle:
            return _Handle()

    async def _connect(*_: object, **__: object) -> _Client:
        return _Client()

    monkeypatch.setattr("temporalio.client.Client.connect", _connect)

    async with _signed_in(accounts[Role.ADMIN]) as client:
        response = await client.post(f"/api/runs/{suspended_run}/terminate")

    assert response.status_code == 202
    assert response.json()["branch_left_behind"] == "kuwarden/pay-1234-abcd1234"
    assert terminated
    assert accounts[Role.ADMIN] in terminated[0]

    async with connect() as conn:
        status = await conn.fetchval("SELECT status FROM flow_runs WHERE id = $1", suspended_run)
        payload = await conn.fetchval(
            "SELECT payload FROM flow_events WHERE run_id = $1 AND kind = 'run_terminated'",
            suspended_run,
        )
    assert status == "terminated", "not 'aborted' — the flow did not decide this"
    recorded = json.loads(payload)
    assert recorded["branch_left_behind"] == "kuwarden/pay-1234-abcd1234"
    assert "compensation did not run" in recorded["detail"]


async def test_a_finished_run_cannot_be_terminated(
    accounts: dict[Role, str], suspended_run: uuid.UUID
) -> None:
    """Nothing to stop, and a `terminated` row over a succeeded run would rewrite its outcome."""
    async with connect() as conn:
        await conn.execute("UPDATE flow_runs SET status = 'succeeded' WHERE id = $1", suspended_run)

    async with _signed_in(accounts[Role.ADMIN]) as client:
        refused = await client.post(f"/api/runs/{suspended_run}/terminate")

    assert refused.status_code == 409
    assert "there is nothing to stop" in refused.json()["detail"]


async def test_an_approver_cannot_terminate_a_run(
    accounts: dict[Role, str], suspended_run: uuid.UUID
) -> None:
    """Approving is a judgment about the change; killing a run is an act on the platform.

    An approver who disagrees with a change rejects it at the gate, which compensates and
    leaves no branch. Terminating skips all of that, so it is deliberately a different role.
    """
    async with _signed_in(accounts[Role.APPROVER]) as client:
        refused = await client.post(f"/api/runs/{suspended_run}/terminate")
    assert refused.status_code == 403


async def test_the_evidence_shows_the_tier_the_gate_actually_used(
    accounts: dict[Role, str], suspended_run: uuid.UUID
) -> None:
    """An approver must be shown the tier that put the decision in front of them.

    `flow_runs.risk_tier` is written once, at run start, and holds the *provisional* tier
    intake guessed from the ticket's labels. Final tiering runs later, over the actual diff,
    and only ever raises. Reading the column here showed "risk tier: low" on a page that was
    demanding two signatures because a change had touched `app/layout.tsx` and been raised to
    high — the document understating the very reason it existed.

    The reason travels with it. "It is high" and "intake guessed low and the diff raised it,
    because of this rule" are different facts, and an approver can only weigh the second.
    """
    async with connect() as conn:
        await conn.execute(
            "INSERT INTO flow_events (run_id, seq, kind, node_id, payload) "
            "VALUES ($1, 9, 'risk_tier_final', NULL, $2::jsonb)",
            suspended_run,
            json.dumps(
                {
                    "tier": "high",
                    "provisional": "low",
                    "reason": "app/layout.tsx matches high_paths '**/layout.*'",
                    "files_changed": 1,
                }
            ),
        )

    async with _signed_in(accounts[Role.VIEWER]) as client:
        document = (await client.get(f"/api/runs/{suspended_run}/evidence")).json()["document"]

    assert document["risk_tier"] == "high", "the authoritative tier, not the intake guess"
    assert document["provisional_risk_tier"] == "low"
    assert "high_paths" in document["risk_tier_reason"]
