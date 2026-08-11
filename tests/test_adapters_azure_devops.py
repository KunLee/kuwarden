"""Azure DevOps adapters, against recorded response shapes.

`httpx.MockTransport` rather than a live organisation: an adapter test that needs a network
is a test nobody runs. What is asserted is the request KuWarden makes — method, path, body —
because that is the part a platform will reject at the worst possible moment.
"""

from __future__ import annotations

import json

import httpx
import pytest

from engine.adapters.credentials import (
    CredentialKind,
    CredentialRequest,
    EnvCredentialBroker,
    Secret,
)
from engine.adapters.protocols import (
    BranchRef,
    FileEdit,
    IntegrationModel,
    RepoRef,
    ScmCapabilities,
    TicketRef,
    validate_integration_model,
)
from engine.adapters.scm.azure_repos import EMPTY_OBJECT_ID, AzureReposScm
from engine.adapters.ticket.azure_devops import AzureDevOpsTickets
from engine.errors import AdapterError, PolicyDenied

BROKER = EnvCredentialBroker({"KUWARDEN_TICKET_TOKEN": "pat-t", "KUWARDEN_SCM_TOKEN": "pat-s"})
TICKET = TicketRef(system="azure_devops", project="Payments", id="1234")
REPO = RepoRef(host="dev.azure.com", org="acme", repo="payments-service", project="Payments")


def _transport(routes: dict[str, object], seen: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        for path, body in routes.items():
            if request.url.path.endswith(path):
                return httpx.Response(200, json=body)
        return httpx.Response(404, text=f"no route for {request.url.path}")

    return httpx.MockTransport(handler)


# --- the credential boundary --------------------------------------------------------------


def test_a_secret_will_not_render_itself() -> None:
    secret = Secret("pat-abc123")
    assert "pat-abc123" not in repr(secret)
    assert "pat-abc123" not in str(secret)
    assert "pat-abc123" not in f"{secret}"
    assert "pat-abc123" not in json.dumps({"token": repr(secret)})
    assert secret.reveal() == "pat-abc123"


def test_a_secret_refuses_to_be_hashed() -> None:
    """Hashing puts it in a cache key, which is a copy nobody remembers making."""
    with pytest.raises(TypeError, match="not hashable"):
        hash(Secret("pat"))


async def test_a_missing_credential_is_denied_not_defaulted() -> None:
    with pytest.raises(PolicyDenied, match="no credential available"):
        await EnvCredentialBroker({}).resolve(
            CredentialRequest(kind=CredentialKind.DEPLOY, realm="acme")
        )


async def test_realm_scoped_credentials_win() -> None:
    broker = EnvCredentialBroker(
        {"KUWARDEN_SCM_TOKEN": "shared", "KUWARDEN_SCM_TOKEN__DEV_AZURE_COM_ACME": "scoped"}
    )
    resolved = await broker.resolve(
        CredentialRequest(kind=CredentialKind.SCM_READ, realm="dev.azure.com:acme")
    )
    assert resolved.reveal() == "scoped"


# --- ADR 0004 registration ----------------------------------------------------------------


def test_model_c_is_refused_where_the_platform_cannot_pause() -> None:
    verdict = validate_integration_model(
        IntegrationModel.GATED_DEPLOYMENT, ScmCapabilities(deployment_protection=False)
    )
    assert not verdict.achievable
    assert "deployment protection" in verdict.reason


def test_model_a_is_refused_without_restrictable_triggers() -> None:
    """Otherwise merging deploys alongside KuWarden — a double deploy, or a race."""
    verdict = validate_integration_model(IntegrationModel.KUWARDEN_DEPLOYS, ScmCapabilities())
    assert not verdict.achievable
    assert "double deploy" in verdict.reason


def test_a_supported_model_is_accepted() -> None:
    verdict = validate_integration_model(
        IntegrationModel.GATED_DEPLOYMENT, ScmCapabilities(deployment_protection=True)
    )
    assert verdict.achievable


async def test_probe_reports_what_it_could_not_establish() -> None:
    """A guess presented as a fact is worse than an admitted gap."""
    seen: list[httpx.Request] = []
    scm = AzureReposScm(
        BROKER,
        transport=_transport(
            {"checks/configurations": {"value": []}, "policy/configurations": {"value": []}}, seen
        ),
    )
    capabilities = await scm.probe(REPO)
    assert capabilities.deployment_protection is True
    assert capabilities.required_status_checks is True
    assert capabilities.restrictable_pipeline_triggers is False
    assert "operator attestation" in capabilities.detail["pipeline_triggers"]


# --- work items ---------------------------------------------------------------------------


async def test_a_work_item_becomes_a_ticket() -> None:
    seen: list[httpx.Request] = []
    tickets = AzureDevOpsTickets(
        "acme",
        BROKER,
        transport=_transport(
            {
                "workitems/1234": {
                    "id": 1234,
                    "fields": {
                        "System.Title": "Add a health endpoint",
                        "System.Description": "<div>Return <b>200</b>&nbsp;from /health</div>",
                        "System.Tags": "kuwarden-auto; backend",
                        "Microsoft.VSTS.Common.AcceptanceCriteria": "<ul><li>200 OK</li></ul>",
                        "Microsoft.VSTS.Scheduling.StoryPoints": 3,
                    },
                }
            },
            seen,
        ),
    )
    ticket = await tickets.fetch(TICKET)

    assert ticket.id == "1234"
    assert ticket.system == "azure_devops"
    assert ticket.title == "Add a health endpoint"
    assert ticket.body == "Return 200 from /health", "markup stripped, text preserved"
    assert ticket.labels == ["kuwarden-auto", "backend"]
    assert ticket.acceptance_criteria == ["200 OK"]
    assert ticket.story_points == 3


async def test_the_pat_travels_as_basic_auth_with_an_empty_user() -> None:
    seen: list[httpx.Request] = []
    tickets = AzureDevOpsTickets(
        "acme", BROKER, transport=_transport({"workitems/1234": {"id": 1234, "fields": {}}}, seen)
    )
    await tickets.fetch(TICKET)
    assert seen[0].headers["Authorization"].startswith("Basic ")


async def test_a_transition_is_sent_as_a_json_patch() -> None:
    """Azure DevOps rejects a plain JSON body on this endpoint."""
    seen: list[httpx.Request] = []
    tickets = AzureDevOpsTickets(
        "acme", BROKER, transport=_transport({"workitems/1234": {}}, seen)
    )
    await tickets.transition(TICKET, "Active")

    request = seen[0]
    assert request.method == "PATCH"
    assert request.headers["Content-Type"] == "application/json-patch+json"
    assert json.loads(request.content) == [
        {"op": "add", "path": "/fields/System.State", "value": "Active"}
    ]


async def test_a_platform_error_becomes_a_typed_one_without_the_token() -> None:
    tickets = AzureDevOpsTickets(
        "acme",
        BROKER,
        transport=httpx.MockTransport(lambda r: httpx.Response(403, text="TF401019: denied")),
    )
    with pytest.raises(AdapterError) as caught:
        await tickets.fetch(TICKET)
    assert "403" in str(caught.value)
    assert "pat-t" not in str(caught.value)


# --- pushing a branch ---------------------------------------------------------------------


async def test_a_new_file_is_pushed_as_add_and_an_existing_one_as_edit() -> None:
    """Azure DevOps rejects `add` for a path that exists, and `edit` for one that does not."""
    seen: list[httpx.Request] = []
    scm = AzureReposScm(
        BROKER,
        transport=_transport(
            {
                # A filter matching nothing: the branch does not exist yet.
                "/refs": {"value": []},
                "/items": {"value": [{"path": "/README.md", "isFolder": False}]},
                "/pushes": {"commits": [{"commitId": "abc1234"}]},
            },
            seen,
        ),
    )
    result = await scm.push_change(
        REPO,
        BranchRef(name="main", commit="base999"),
        branch="kuwarden/1234",
        message="PAY-1234",
        edits=[FileEdit("README.md", "changed"), FileEdit("NEW.md", "created")],
    )

    push = json.loads(next(r for r in seen if r.url.path.endswith("/pushes")).content)
    changes = {c["item"]["path"]: c["changeType"] for c in push["commits"][0]["changes"]}
    assert changes == {"/README.md": "edit", "/NEW.md": "add"}
    assert push["refUpdates"] == [
        {"name": "refs/heads/kuwarden/1234", "oldObjectId": "base999"}
    ]
    assert result.commit == "abc1234"


async def test_a_branch_is_created_at_the_base_before_anything_is_pushed_onto_it() -> None:
    """The zero object id creates a ref; it does not parent a commit.

    Passing it on the push itself — which an earlier version effectively did — would produce a
    commit with no parent, i.e. a branch containing the change and nothing else.
    """
    seen: list[httpx.Request] = []
    scm = AzureReposScm(
        BROKER,
        transport=_transport(
            {
                "/refs": {"value": []},
                "/items": {"value": []},
                "/pushes": {"commits": [{"commitId": "abc1234"}]},
            },
            seen,
        ),
    )
    await scm.push_change(
        REPO,
        BranchRef(name="main", commit="base999"),
        branch="kuwarden/1234",
        message="PAY-1234",
        edits=[FileEdit("NEW.md", "created")],
    )

    created = json.loads(
        next(r for r in seen if r.method == "POST" and r.url.path.endswith("/refs")).content
    )
    assert created == [
        {
            "name": "refs/heads/kuwarden/1234",
            "oldObjectId": EMPTY_OBJECT_ID,
            "newObjectId": "base999",
        }
    ]


async def test_a_push_that_already_landed_is_not_repeated() -> None:
    """Temporal retries an activity whose acknowledgement was lost — ADR 0007.

    The commit message is the idempotency key, so the adapter recognises its own work on the
    branch instead of committing it a second time.
    """
    seen: list[httpx.Request] = []
    scm = AzureReposScm(
        BROKER,
        transport=_transport(
            {
                "/refs": {"value": [{"objectId": "abc1234"}]},
                "/commits/abc1234": {"comment": "PAY-1234"},
            },
            seen,
        ),
    )
    result = await scm.push_change(
        REPO,
        BranchRef(name="main", commit="base999"),
        branch="kuwarden/1234",
        message="PAY-1234",
        edits=[FileEdit("NEW.md", "created")],
    )

    assert result.commit == "abc1234"
    assert not [r for r in seen if r.url.path.endswith("/pushes")], "nothing was pushed again"


async def test_a_branch_that_moved_is_refused_rather_than_overwritten() -> None:
    """Someone else wrote here. Force-pushing over it destroys evidence."""
    scm = AzureReposScm(
        BROKER,
        transport=_transport(
            {
                "/refs": {"value": [{"objectId": "somebody-else"}]},
                "/commits/somebody-else": {"comment": "unrelated work"},
            },
            [],
        ),
    )
    with pytest.raises(AdapterError, match="refusing to overwrite"):
        await scm.push_change(
            REPO,
            BranchRef(name="main", commit="base999"),
            branch="kuwarden/1234",
            message="PAY-1234",
            edits=[FileEdit("NEW.md", "created")],
        )


async def test_an_empty_change_is_refused() -> None:
    scm = AzureReposScm(BROKER, transport=_transport({}, []))
    with pytest.raises(AdapterError, match="empty change"):
        await scm.push_change(REPO, BranchRef("main", "x"), "b", "m", edits=[])


async def test_the_default_branch_resolves_to_a_commit() -> None:
    seen: list[httpx.Request] = []
    scm = AzureReposScm(
        BROKER,
        transport=_transport(
            {
                "/repositories/payments-service": {"defaultBranch": "refs/heads/main"},
                "/refs": {"value": [{"objectId": "deadbeef"}]},
            },
            seen,
        ),
    )
    branch = await scm.default_branch(REPO)
    assert branch == BranchRef(name="main", commit="deadbeef")


async def test_opening_a_pull_request_returns_a_usable_url() -> None:
    seen: list[httpx.Request] = []
    scm = AzureReposScm(
        BROKER, transport=_transport({"/pullrequests": {"pullRequestId": 42}}, seen)
    )
    pr = await scm.open_pull_request(REPO, "kuwarden/1234", "main", "PAY-1234", "body")

    assert pr.id == "42"
    assert pr.url.endswith("/acme/Payments/_git/payments-service/pullrequest/42")
    body = json.loads(seen[0].content)
    assert body["sourceRefName"] == "refs/heads/kuwarden/1234"
    assert body["targetRefName"] == "refs/heads/main"


async def test_an_azure_ping_reads_the_project_not_the_account() -> None:
    """Proves the token *and* that the project name is right — see `TicketAdapter.ping`."""
    seen: list[httpx.Request] = []
    routes: dict[str, object] = {"/_apis/projects/Payments": {"name": "Payments"}}
    tickets = AzureDevOpsTickets("acme", BROKER, transport=_transport(routes, seen))

    assert await tickets.ping(TICKET) == "acme/Payments"
    assert seen[0].url.path.endswith("/_apis/projects/Payments")


async def test_an_azure_ping_fails_loudly_for_an_unknown_project() -> None:
    tickets = AzureDevOpsTickets("acme", BROKER, transport=_transport({}, []))
    with pytest.raises(AdapterError):
        await tickets.ping(TICKET)


async def test_a_work_item_carries_its_state() -> None:
    """Admission may require a specific state, so it has to reach `Ticket` — not be dropped."""
    routes: dict[str, object] = {
        "workitems/1234": {
            "id": 1234,
            "fields": {"System.Title": "x", "System.State": "Ready for Agent"},
        }
    }
    tickets = AzureDevOpsTickets("acme", BROKER, transport=_transport(routes, []))
    assert (await tickets.fetch(TICKET)).state == "Ready for Agent"
