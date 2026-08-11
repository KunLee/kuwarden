"""Jira and GitHub adapters, against recorded response shapes."""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from engine.adapters.credentials import EnvCredentialBroker
from engine.adapters.protocols import BranchRef, FileEdit, RepoRef, TicketRef
from engine.adapters.scm.github import GitHubScm
from engine.adapters.ticket.jira import JiraTickets
from engine.errors import AdapterError

BROKER = EnvCredentialBroker({"KUWARDEN_TICKET_TOKEN": "jira-t", "KUWARDEN_SCM_TOKEN": "gh-t"})
ISSUE = TicketRef(system="jira", project="PAY", id="PAY-1234")
REPO = RepoRef(host="github.com", org="acme", repo="payments-service")


def _transport(routes: dict[str, object], seen: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        for path, body in routes.items():
            if request.url.path.endswith(path):
                return httpx.Response(200, json=body)
        return httpx.Response(404, text=f"no route for {request.url.path}")

    return httpx.MockTransport(handler)


def _jira(routes: dict[str, object], seen: list[httpx.Request], **kw: object) -> JiraTickets:
    return JiraTickets(
        "https://acme.atlassian.net",
        "bot@acme.test",
        BROKER,
        transport=_transport(routes, seen),
        **kw,  # type: ignore[arg-type]
    )


# --- Jira ---------------------------------------------------------------------------------

ADF_DESCRIPTION = {
    "type": "doc",
    "version": 1,
    "content": [
        {
            "type": "paragraph",
            "content": [
                {"type": "text", "text": "Return "},
                {"type": "text", "text": "200", "marks": [{"type": "strong"}]},
                {"type": "text", "text": " from /health"},
            ],
        },
        {
            "type": "bulletList",
            "content": [
                {
                    "type": "listItem",
                    "content": [
                        {"type": "paragraph", "content": [{"type": "text", "text": "no auth"}]}
                    ],
                }
            ],
        },
    ],
}


async def test_adf_description_is_flattened_to_text() -> None:
    """A regex over ADF would return JSON punctuation; the tree has to be walked."""
    seen: list[httpx.Request] = []
    jira = _jira(
        {
            "/issue/PAY-1234": {
                "key": "PAY-1234",
                "fields": {
                    "summary": "Add a health endpoint",
                    "description": ADF_DESCRIPTION,
                    "labels": ["kuwarden-auto"],
                },
            }
        },
        seen,
    )
    ticket = await jira.fetch(ISSUE)

    assert ticket.system == "jira"
    assert ticket.id == "PAY-1234"
    assert ticket.body == "Return 200 from /health\nno auth"
    assert ticket.labels == ["kuwarden-auto"]
    assert "{" not in ticket.body
    assert "type" not in ticket.body


async def test_story_points_are_none_when_the_field_is_not_configured() -> None:
    """The custom field id differs per instance; assuming one would read the wrong field."""
    seen: list[httpx.Request] = []
    payload = {"key": "PAY-1", "fields": {"summary": "x", "customfield_10016": 5}}
    assert (await _jira({"/issue/PAY-1234": payload}, seen).fetch(ISSUE)).story_points is None

    configured = _jira({"/issue/PAY-1234": payload}, seen, story_points_field="customfield_10016")
    assert (await configured.fetch(ISSUE)).story_points == 5


async def test_a_comment_is_posted_as_an_adf_document() -> None:
    seen: list[httpx.Request] = []
    await _jira({"/comment": {}}, seen).comment(ISSUE, "PR raised: #42")

    body = json.loads(seen[0].content)
    assert body["body"]["type"] == "doc"
    assert body["body"]["content"][0]["content"][0]["text"] == "PR raised: #42"


async def test_a_transition_is_resolved_by_name_not_guessed() -> None:
    seen: list[httpx.Request] = []
    jira = _jira(
        {"/transitions": {"transitions": [{"id": "31", "name": "In Progress"}]}}, seen
    )
    await jira.transition(ISSUE, "in progress")

    posted = [r for r in seen if r.method == "POST"]
    assert json.loads(posted[0].content) == {"transition": {"id": "31"}}


async def test_an_unavailable_transition_says_what_was_available() -> None:
    seen: list[httpx.Request] = []
    jira = _jira({"/transitions": {"transitions": [{"id": "31", "name": "Done"}]}}, seen)
    with pytest.raises(AdapterError, match="available: Done"):
        await jira.transition(ISSUE, "Deployed")


# --- GitHub -------------------------------------------------------------------------------


async def test_a_multi_file_change_becomes_exactly_one_commit() -> None:
    """The Contents API would produce one commit per file — a diff nobody asked for."""
    seen: list[httpx.Request] = []
    scm = GitHubScm(
        BROKER,
        transport=_transport(
            {
                "/git/commits/base999": {"tree": {"sha": "tree-base"}},
                "/git/blobs": {"sha": "blob-sha"},
                "/git/trees": {"sha": "tree-new"},
                "/git/commits": {"sha": "commit-new"},
                "/git/refs": {},
            },
            seen,
        ),
    )
    result = await scm.push_change(
        REPO,
        BranchRef(name="main", commit="base999"),
        branch="kuwarden/PAY-1234",
        message="PAY-1234",
        edits=[FileEdit("src/health.py", "ok"), FileEdit("README.md", "docs")],
    )

    commits = [r for r in seen if r.url.path.endswith("/git/commits") and r.method == "POST"]
    assert len(commits) == 1, "one commit, not one per file"
    assert json.loads(commits[0].content)["parents"] == ["base999"]

    ref_create = json.loads(next(r for r in seen if r.url.path.endswith("/git/refs")).content)
    assert ref_create == {"ref": "refs/heads/kuwarden/PAY-1234", "sha": "commit-new"}
    assert result.commit == "commit-new"


async def test_an_empty_repository_says_so_rather_than_reporting_a_404() -> None:
    """A repo with no commits still reports a `default_branch`, but the ref does not exist.

    Left as a bare 404 this reads like a bad token or a mistyped repository, and the operator
    spends their time regenerating a credential that was fine.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/git/ref/heads/main"):
            return httpx.Response(404, json={"message": "Git Repository is empty."})
        return httpx.Response(200, json={"default_branch": "main"})

    scm = GitHubScm(BROKER, transport=httpx.MockTransport(handler))
    with pytest.raises(AdapterError, match="the repository is empty"):
        await scm.default_branch(REPO)


async def test_an_existing_branch_is_fast_forwarded_and_never_forced() -> None:
    """The second attempt of a run — ADR 0007. `force` is what would erase the first.

    Asserted at the request level because that is where the claim lives: a `force: true` here
    would still pass every test that only reads the returned `BranchRef`.
    """
    seen: list[httpx.Request] = []
    scm = GitHubScm(
        BROKER,
        transport=_transport(
            {
                "/git/ref/heads/kuwarden/PAY-1234": {"object": {"sha": "attempt-1"}},
                "/git/commits/attempt-1": {"message": "an earlier attempt"},
                "/git/commits/base999": {"tree": {"sha": "tree-base"}},
                "/git/blobs": {"sha": "blob-sha"},
                "/git/trees": {"sha": "tree-new"},
                "/git/commits": {"sha": "attempt-2"},
                "/git/refs/heads/kuwarden/PAY-1234": {},
            },
            seen,
        ),
    )
    result = await scm.push_change(
        REPO,
        BranchRef(name="main", commit="base999"),
        branch="kuwarden/PAY-1234",
        message="PAY-1234 second attempt",
        edits=[FileEdit("src/health.py", "ok")],
        parent="attempt-1",
    )

    patched = next(r for r in seen if r.method == "PATCH")
    assert json.loads(patched.content) == {"sha": "attempt-2", "force": False}
    assert not [r for r in seen if r.method == "POST" and r.url.path.endswith("/git/refs")]
    # The tree still comes from the pinned base, so the branch holds base + this attempt.
    tree = json.loads(next(r for r in seen if r.url.path.endswith("/git/trees")).content)
    assert tree["base_tree"] == "tree-base"
    assert result.commit == "attempt-2"


async def test_a_push_that_already_landed_is_not_repeated() -> None:
    """The commit message is the idempotency key for a retried activity — ADR 0007."""
    seen: list[httpx.Request] = []
    scm = GitHubScm(
        BROKER,
        transport=_transport(
            {
                "/git/ref/heads/kuwarden/PAY-1234": {"object": {"sha": "already-there"}},
                "/git/commits/already-there": {"message": "PAY-1234"},
            },
            seen,
        ),
    )
    result = await scm.push_change(
        REPO,
        BranchRef(name="main", commit="base999"),
        branch="kuwarden/PAY-1234",
        message="PAY-1234",
        edits=[FileEdit("src/health.py", "ok")],
    )

    assert result.commit == "already-there"
    assert not [r for r in seen if r.method in {"POST", "PATCH"}], "nothing was written twice"


async def test_blob_content_is_base64_encoded() -> None:
    seen: list[httpx.Request] = []
    scm = GitHubScm(
        BROKER,
        transport=_transport(
            {
                "/git/commits/b": {"tree": {"sha": "t"}},
                "/git/blobs": {"sha": "s"},
                "/git/trees": {"sha": "t2"},
                "/git/commits": {"sha": "c"},
                "/git/refs": {},
            },
            seen,
        ),
    )
    await scm.push_change(
        REPO, BranchRef("main", "b"), "br", "m", edits=[FileEdit("a.txt", "héllo")]
    )
    blob = json.loads(next(r for r in seen if r.url.path.endswith("/git/blobs")).content)
    assert base64.b64decode(blob["content"]).decode() == "héllo"


async def test_the_token_travels_as_a_bearer() -> None:
    seen: list[httpx.Request] = []
    scm = GitHubScm(
        BROKER,
        transport=_transport(
            {"/repos/acme/payments-service": {"default_branch": "main"},
             "/git/ref/heads/main": {"object": {"sha": "abc"}}},
            seen,
        ),
    )
    await scm.default_branch(REPO)
    assert seen[0].headers["Authorization"] == "Bearer gh-t"
    assert seen[0].headers["X-GitHub-Api-Version"] == "2022-11-28"


BASE_PROBE_ROUTES: dict[str, object] = {
    "/repos/acme/payments-service": {"default_branch": "main"},
    "/git/ref/heads/main": {"object": {"sha": "abc"}},
}


async def test_probe_records_that_a_404_is_ambiguous() -> None:
    """No protection rule and no admin scope look identical to the API. Say so."""
    scm = GitHubScm(
        BROKER,
        transport=_transport(
            {**BASE_PROBE_ROUTES, "/environments": {"total_count": 0, "environments": []}},
            [],
        ),
    )
    capabilities = await scm.probe(REPO)

    assert capabilities.required_status_checks is False
    assert "does not distinguish" in capabilities.detail["branch_protection"]
    assert "operator attestation" in capabilities.detail["workflow_triggers"]


async def test_a_403_is_not_reported_as_the_404_ambiguity() -> None:
    """A 403 says exactly what is wrong; the 404 note would send the reader elsewhere.

    The operator whose fine-grained token lacks Administration gets told that, rather than
    being invited to wonder whether a protection rule exists.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/protection"):
            return httpx.Response(403, json={"message": "Resource not accessible by PAT"})
        for path, body in {
            **BASE_PROBE_ROUTES,
            "/environments": {"total_count": 0},
        }.items():
            if request.url.path.endswith(path):
                return httpx.Response(200, json=body)
        return httpx.Response(404, text="no route")

    scm = GitHubScm(BROKER, transport=httpx.MockTransport(handler))
    capabilities = await scm.probe(REPO)

    detail = capabilities.detail["branch_protection"]
    assert "403" in detail
    assert "does not distinguish" not in detail, "the 404 ambiguity note must not appear on a 403"


async def test_a_repository_with_no_environments_cannot_gate_a_deployment() -> None:
    """The endpoint replying is not a control point.

    A repository with zero environments answers 200 with an empty list. Reading that as
    "model C is achievable" declares a control point over a deployment that has nothing to
    pause — the exact overstatement ADR 0004 §2 exists to prevent.
    """
    scm = GitHubScm(
        BROKER,
        transport=_transport(
            {**BASE_PROBE_ROUTES, "/environments": {"total_count": 0, "environments": []}}, []
        ),
    )
    capabilities = await scm.probe(REPO)

    assert capabilities.deployment_protection is False
    assert "no environment is configured" in capabilities.detail["environments"]


async def test_a_repository_with_environments_can_gate_a_deployment() -> None:
    scm = GitHubScm(
        BROKER,
        transport=_transport(
            {
                **BASE_PROBE_ROUTES,
                "/environments": {"total_count": 2, "environments": [{"name": "prod"}]},
            },
            [],
        ),
    )
    capabilities = await scm.probe(REPO)

    assert capabilities.deployment_protection is True
    assert "2 configured" in capabilities.detail["environments"]


async def test_a_pull_request_returns_the_platform_url() -> None:
    seen: list[httpx.Request] = []
    scm = GitHubScm(
        BROKER,
        transport=_transport(
            {"/pulls": {"number": 42, "html_url": "https://github.com/acme/x/pull/42"}}, seen
        ),
    )
    pr = await scm.open_pull_request(REPO, "kuwarden/PAY-1234", "main", "PAY-1234", "body")

    assert pr.id == "42"
    assert pr.url == "https://github.com/acme/x/pull/42"
    assert json.loads(seen[0].content)["head"] == "kuwarden/PAY-1234"


async def test_a_jira_ping_reads_the_project_not_the_account() -> None:
    """An "am I authenticated" check passes with a good token and a mistyped project.

    Which is the mistake operators actually make, so the ping is scoped to the project.
    """
    seen: list[httpx.Request] = []
    jira = _jira({"/project/PAY": {"name": "Payments", "key": "PAY"}}, seen)

    assert await jira.ping(ISSUE) == "https://acme.atlassian.net/Payments"
    assert seen[0].url.path.endswith("/project/PAY")


async def test_a_jira_ping_fails_loudly_for_an_unknown_project() -> None:
    jira = _jira({}, [])
    with pytest.raises(AdapterError):
        await jira.ping(ISSUE)


async def test_a_jira_issue_carries_its_status_as_state() -> None:
    """Jira nests it under `status`; Azure DevOps has a flat field. One normalised name."""
    payload = {
        "key": "PAY-1234",
        "fields": {"summary": "x", "status": {"name": "Ready for Agent"}},
    }
    jira = _jira({"/issue/PAY-1234": payload}, [])
    assert (await jira.fetch(ISSUE)).state == "Ready for Agent"
