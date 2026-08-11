"""Shared fixtures.

The mocked platform here is what lets the end-to-end flow tests run the *real* nodes —
adapters, credential resolution, protected-path enforcement and all — without an Azure DevOps
organisation, a Jira site, or a GitHub repository. The Flow Engine, Temporal and PostgreSQL
are real; only the far side of the HTTP boundary is not.
"""

from __future__ import annotations

import asyncio
import base64
import json
import uuid

import httpx
import pytest

from engine.activities.nodes import RUNTIME
from engine.adapters.credentials import EnvCredentialBroker
from engine.config import AppConfig, parse
from engine.devenv import load_dotenv
from engine.sandbox import ExecResult, ResourceLimits, SandboxCapabilities, Workspace

# The walking-skeleton tests talk to the real stack, which needs the .env compose read.
load_dotenv()

#: Application rows the suite inserted, torn down when the session ends.
#:
#: These tests run against the developer's own PostgreSQL, and every registered application
#: shows up in their Workbench. Left behind, a few suite runs bury the operator's real
#: applications under hundreds of `test-app-*` rows — which is not a cosmetic problem: the
#: point of the Workbench is to show what this deployment is configured to touch, and a list
#: nobody can read is a control nobody exercises.
_REGISTERED: list[uuid.UUID] = []


def track_application(app_id: uuid.UUID) -> uuid.UUID:
    """Record an application row for teardown. Returns it, so it can wrap an insert."""
    _REGISTERED.append(app_id)
    return app_id


async def _purge() -> None:
    """Delete tracked applications and everything hanging off them.

    Ordered by foreign key: events, then runs, then the application's own rows. `flow_events`
    carries the append-only trigger from invariant 9, so it is disabled for exactly this
    statement and re-enabled in a `finally` — a teardown that left the audit table unprotected
    would be a worse bug than the one it is cleaning up after.
    """
    if not _REGISTERED:
        return
    from engine.db import connect

    ids = list(_REGISTERED)
    _REGISTERED.clear()
    async with connect() as conn, conn.transaction():
        await conn.execute("ALTER TABLE flow_events DISABLE TRIGGER flow_events_no_update")
        try:
            await conn.execute(
                "DELETE FROM flow_events WHERE run_id IN "
                "(SELECT id FROM flow_runs WHERE app_id = ANY($1::uuid[]))",
                ids,
            )
            await conn.execute("DELETE FROM flow_runs WHERE app_id = ANY($1::uuid[])", ids)
        finally:
            await conn.execute("ALTER TABLE flow_events ENABLE TRIGGER flow_events_no_update")
        await conn.execute("DELETE FROM app_credentials WHERE app_id = ANY($1::uuid[])", ids)
        await conn.execute("DELETE FROM app_triggers WHERE app_id = ANY($1::uuid[])", ids)
        await conn.execute("DELETE FROM app_registry WHERE id = ANY($1::uuid[])", ids)


@pytest.fixture(scope="session", autouse=True)
def _clean_up_registered_applications():  # type: ignore[no-untyped-def]
    """Session-wide teardown. Synchronous on purpose: a session-scoped *async* fixture needs
    its own event loop scope, and getting that subtly wrong fails as a hang rather than as an
    error. `asyncio.run` at teardown has neither problem."""
    yield
    try:
        asyncio.run(_purge())
    except Exception as exc:  # noqa: BLE001 — a suite must not fail because cleanup could not run
        print(f"\n[warn] could not purge test applications: {exc}")

KUWARDEN_YAML = """
version: 1

app:
  name: payments-service

workspace:
  repos:
    - name: payments-service
      provider: github
      org: acme
      repo: payments-service

triggers:
  - provider: jira
    site: https://acme.atlassian.net
    account_email: bot@acme.test
    project: PAY
    label: kuwarden-auto
    ready_state: Ready for Agent
    max_story_points: 5

delivery:
  integration_model: gated_deployment

toolchain:
  id: python3.12

sandbox:
  toolchain_image: localhost/kuwarden-python312:1
  require_full_isolation: false
  test_command: [python, -m, pytest, -q]
  limits: { memory_mb: 512, timeout_s: 60, tmp_mb: 64 }

# Short waits so a test that exercises "no pipeline appeared" finishes in seconds rather
# than in the minutes a real deployment would allow for.
ci:
  provider: github_actions
  wait_s: 6
  poll_s: 1
  grace_s: 2

llm:
  provider: anthropic
  planner: { model: claude-opus-5, effort: high }
  coder: { model: claude-opus-5, effort: xhigh }
  verifiers: { model: claude-opus-5, effort: high }

risk:
  high_labels: [security, payments]

budgets:
  cents_per_run: 500
"""


class FakePlatform:
    """Records every request so a test can assert what KuWarden actually sent."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.pull_requests: list[dict[str, object]] = []
        #: Branch name -> commit sha, as the fake repository actually holds them. Real state
        #: rather than a canned response: the push path branches on whether a ref exists, so a
        #: fake that always answered the same way would exercise only one half of it.
        self.branches: dict[str, str] = {}
        #: Commit sha -> the body that created it. Lets a test read a commit's message and
        #: parents back, which is what the adapter's idempotency check reads.
        self.commits: dict[str, dict[str, object]] = {}
        #: What the fake project's pipeline reports. `ci_has_pipeline = False` is the
        #: repository that simply has no CI — the case that must never read as a pass.
        self.ci_has_pipeline: bool = True
        self.ci_status: str = "completed"
        self.ci_conclusion: str = "success"
        self.comments: list[str] = []
        self.labels: list[str] = ["kuwarden-auto"]
        #: The workflow state the fake ticket reports. Admission may require a specific
        #: one, so a test has to be able to put the ticket in the wrong state.
        self.ticket_state: str = "Ready for Agent"
        self.story_points: int | None = 3
        #: What the fake verifiers return. `verifier_blocks = True` is the case the whole
        #: topology exists for — a change that does not survive review.
        self.verifier_blocks: bool = False
        self.verifier_findings: list[str] = []
        #: Set to a category string to make the model decline the next completion.
        self.refuse_with: str | None = None
        self.messages_requests: list[dict[str, object]] = []
        #: The target repository's contents, as the tree endpoints will serve them.
        self.repo_files: dict[str, str] = {
            "src/app.py": "def add(a, b):\n    return a + b\n",
            "tests/test_app.py": (
                "from src.app import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"
            ),
            "README.md": "# demo\n",
        }
        #: What the Coder's model "writes". Replaced per test to drive the inner loop.
        self.coder_edits_factory: object | None = None
        self.coder_edits: list[dict[str, str]] = [
            {
                "path": "src/app.py",
                "content": (
                    "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n"
                ),
            }
        ]

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path

        if path.endswith("/v1/messages"):
            return self._messages(json.loads(request.content))

        if path.endswith("/comment"):
            self.comments.append(json.loads(request.content)["body"]["content"][0]["content"][0]["text"])
            return httpx.Response(201, json={})
        if "/issue/" in path:
            return httpx.Response(
                200,
                json={
                    "key": "PAY-1234",
                    "fields": {
                        "summary": "Add a health endpoint",
                        "description": {
                            "type": "doc",
                            "version": 1,
                            "content": [
                                {
                                    "type": "paragraph",
                                    "content": [{"type": "text", "text": "Return 200"}],
                                }
                            ],
                        },
                        "labels": self.labels,
                        "status": {"name": self.ticket_state},
                        "customfield_points": self.story_points,
                    },
                },
            )
        if path.endswith("/actions/runs"):
            # `head_sha` is echoed rather than fixed. The adapter discards runs belonging to
            # another commit, and a fake that answered with a constant sha would make that
            # guard untestable — and would hide it if someone deleted it.
            head_sha = request.url.params.get("head_sha", "")
            return httpx.Response(200, json={"workflow_runs": self._workflow_runs(head_sha)})
        if path.endswith("/repos/acme/payments-service"):
            return httpx.Response(200, json={"default_branch": "main"})
        if path.endswith("/git/ref/heads/main"):
            return httpx.Response(200, json={"object": {"sha": "base000"}})
        if "/git/ref/heads/" in path:
            # Any other branch: whatever this fake has been pushed, else absent. A 404 is the
            # answer the adapter reads as "create it", so it must be a real 404 and not an
            # empty 200 — the distinction is the whole create-versus-update decision.
            branch = path.split("/git/ref/heads/", 1)[1]
            if branch not in self.branches:
                return httpx.Response(404, json={"message": "Not Found"})
            return httpx.Response(200, json={"object": {"sha": self.branches[branch]}})
        if "/git/trees/" in path:
            return httpx.Response(200, json={
                "truncated": False,
                "tree": [
                    {"type": "blob", "path": p, "sha": p, "size": len(c)}
                    for p, c in self.repo_files.items()
                ],
            })
        if "/git/blobs/" in path:
            sha = path.split("/git/blobs/", 1)[1]
            body = self.repo_files.get(sha, "")
            return httpx.Response(200, json={
                "content": base64.b64encode(body.encode()).decode()
            })
        if "/git/commits/" in path:
            sha = path.split("/git/commits/", 1)[1]
            return httpx.Response(
                200,
                json={
                    "tree": {"sha": "tree-base"},
                    "message": self.commits.get(sha, {}).get("message", ""),
                },
            )
        if path.endswith("/git/blobs"):
            return httpx.Response(201, json={"sha": "blob-1"})
        if path.endswith("/git/trees"):
            return httpx.Response(201, json={"sha": "tree-1"})
        if path.endswith("/git/commits"):
            # A distinct sha per commit, so a test can tell a second push from a duplicate of
            # the first. A fake that returned one constant would make both look identical.
            body = json.loads(request.content)
            sha = f"commit-{len(self.commits) + 1}"
            self.commits[sha] = body
            return httpx.Response(201, json={"sha": sha})
        if path.endswith("/git/refs"):
            body = json.loads(request.content)
            self.branches[str(body["ref"]).removeprefix("refs/heads/")] = str(body["sha"])
            return httpx.Response(201, json={})
        if "/git/refs/heads/" in path:
            branch = path.split("/git/refs/heads/", 1)[1]
            if request.method == "DELETE":
                # Compensation removes a branch this run created. A 404 for one already gone
                # is the answer the adapter treats as "the state you wanted".
                if self.branches.pop(branch, None) is None:
                    return httpx.Response(404, json={"message": "Reference does not exist"})
                return httpx.Response(204)
            self.branches[branch] = str(json.loads(request.content)["sha"])
            return httpx.Response(200, json={})
        if path.endswith("/pulls"):
            body = json.loads(request.content)
            self.pull_requests.append(body)
            return httpx.Response(
                201,
                json={"number": 42, "html_url": "https://github.com/acme/payments-service/pull/42"},
            )
        return httpx.Response(404, text=f"fake platform has no route for {path}")

    def _workflow_runs(self, head_sha: str) -> list[dict[str, object]]:
        """The project's pipeline, as the GitHub Actions listing would report it."""
        if not self.ci_has_pipeline:
            return []
        return [
            {
                "id": 991,
                "name": "CI",
                "path": ".github/workflows/ci.yml",
                "html_url": "https://github.com/acme/payments-service/actions/runs/991",
                "head_sha": head_sha,
                "status": self.ci_status,
                "conclusion": self.ci_conclusion if self.ci_status == "completed" else None,
            }
        ]

    def _messages(self, body: dict[str, object]) -> httpx.Response:
        """The Anthropic Messages API, close enough to catch shape errors.

        A refusal is a 200 with an empty content array — the case that breaks naive code.
        """
        self.messages_requests.append(body)
        usage = {"input_tokens": 120, "output_tokens": 40}

        if self.refuse_with is not None:
            return httpx.Response(200, json={
                "id": "msg_refused", "type": "message", "role": "assistant",
                "model": body.get("model"), "content": [],
                "stop_reason": "refusal",
                "stop_details": {"type": "refusal", "category": self.refuse_with,
                                 "explanation": "declined"},
                "usage": usage,
            })

        # The Planner and the Coder use different schemas. A fake that returned one shape for
        # both would let a schema mismatch pass unnoticed in every flow test.
        system = str(body.get("system", ""))
        if "independent reviewers of a proposed software change" in system:
            # The verdict shape, so the verifier's own parsing is exercised rather than
            # accidentally satisfied by the Planner's payload.
            payload = json.dumps(
                {"findings": self.verifier_findings, "blocks": self.verifier_blocks}
            )
            return httpx.Response(200, json={
                "id": "msg_verdict", "type": "message", "role": "assistant",
                "model": body.get("model"),
                "content": [{"type": "text", "text": payload}],
                "stop_reason": "end_turn", "usage": usage,
            })
        if "You implement one software change" in system:
            edits = (
                self.coder_edits_factory()
                if callable(self.coder_edits_factory)
                else self.coder_edits
            )
            payload = json.dumps({"reasoning": "edit", "edits": edits})
        else:
            payload = json.dumps(
                {"summary": "Add a subtraction helper", "steps": ["write sub", "add a test"]}
            )
        return httpx.Response(200, json={
            "id": "msg_ok", "type": "message", "role": "assistant",
            "model": body.get("model"),
            "content": [{"type": "text", "text": payload}],
            "stop_reason": "end_turn",
            "usage": usage,
        })


class StubSandbox:
    """A sandbox that reports success without starting a container.

    Used by the flow tests, which exist to prove the *control plane* — worker-crash recovery,
    gate suspend and resume, the audit trail. A real container adds several seconds per test
    and no signal those tests are looking for.

    The real thing is proven elsewhere and deliberately: `test_sandbox.py` runs actual
    containers against actual podman, and `test_coder_loop.py` drives the inner loop with a
    real sandbox running real pytest. Layering the suite this way is not the same as mocking
    away the thing under test.
    """

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def capabilities(self) -> SandboxCapabilities:
        return SandboxCapabilities(
            cgroup_memory=True,
            cgroup_cpu=True,
            cgroup_pids=True,
            rlimit_memory=True,
            tmpfs_quota=True,
        )

    async def exec(
        self,
        workspace: Workspace,
        toolchain_id: str,
        command: list[str],
        limits: ResourceLimits,
    ) -> ExecResult:
        self.calls.append(command)
        return ExecResult(
            exit_code=0,
            stdout="",
            stderr="",
            duration_ms=1,
            enforced=await self.capabilities(),
        )


@pytest.fixture
def app_config() -> AppConfig:
    return parse(KUWARDEN_YAML)


@pytest.fixture
def platform(app_config: AppConfig) -> FakePlatform:
    """Binds the worker runtime for the duration of a test."""
    fake = FakePlatform()
    RUNTIME.configure(
        app_config,
        broker=EnvCredentialBroker(
            {
                "KUWARDEN_TICKET_TOKEN": "jira-t",
                "KUWARDEN_SCM_TOKEN": "gh-t",
                "KUWARDEN_LLM_API_KEY": "sk-ant-fake",
            }
        ),
        transport=fake.transport(),
        sandbox=StubSandbox(),
    )
    return fake


@pytest.fixture
def real_sandbox_platform(app_config: AppConfig) -> FakePlatform:
    """Same fake platform, but the sandbox is podman. For tests that are about the sandbox."""
    from engine.sandbox.podman import PodmanSandbox

    fake = FakePlatform()
    RUNTIME.configure(
        app_config,
        broker=EnvCredentialBroker(
            {
                "KUWARDEN_TICKET_TOKEN": "jira-t",
                "KUWARDEN_SCM_TOKEN": "gh-t",
                "KUWARDEN_LLM_API_KEY": "sk-ant-fake",
            }
        ),
        transport=fake.transport(),
        sandbox=PodmanSandbox(require_full_isolation=False),
    )
    return fake
