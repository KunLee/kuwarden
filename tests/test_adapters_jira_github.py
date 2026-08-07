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


async def test_probe_records_that_a_404_is_ambiguous() -> None:
    """No protection rule and no admin scope look identical to the API. Say so."""
    seen: list[httpx.Request] = []
    scm = GitHubScm(
        BROKER,
        transport=_transport(
            {"/repos/acme/payments-service": {"default_branch": "main"},
             "/git/ref/heads/main": {"object": {"sha": "abc"}},
             "/environments": {"environments": []}},
            seen,
        ),
    )
    capabilities = await scm.probe(REPO)

    assert capabilities.deployment_protection is True
    assert capabilities.required_status_checks is False
    assert "admin scope" in capabilities.detail["branch_protection"]
    assert "operator attestation" in capabilities.detail["workflow_triggers"]


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
