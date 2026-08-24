"""The CI adapter, and Build & Test reading a verdict back from it.

This is the machinery invariant 3 has been deviating from since the project started, so the
tests that matter most here are the ones asserting what does **not** happen: absence never
becomes a pass, and a verdict about another commit is never read as a verdict about this one.

A green test here does not mean invariant 3 holds. It means the mechanism that could make it
hold behaves correctly; whether a given application has a pipeline is that application's fact.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from engine.activities.nodes import RUNTIME
from engine.adapters.ci import CiRun, await_verdict
from engine.adapters.ci.github_actions import GitHubActionsCi
from engine.adapters.credentials import EnvCredentialBroker
from engine.adapters.protocols import RepoRef
from engine.config import AppConfig, CiConfig, ConfigError, parse
from engine.errors import AdapterError
from engine.nodes import NODES
from engine.nodes.base import bound
from engine.sandbox import ExecResult, ResourceLimits, SandboxCapabilities, Workspace
from engine.state import Diff, FileChange, FlowState, ProposedEdit, Ticket
from tests.conftest import KUWARDEN_YAML, FakePlatform


class FailingSandbox:
    """A sandbox whose suite fails, so the CI wait can be shown *not* to happen."""

    async def capabilities(self) -> SandboxCapabilities:
        # A host that enforces everything, so these tests exercise the CI wait rather than
        # the weakened-isolation branch. The fields have no defaults on purpose: a sandbox
        # that reports a limit it is not applying is the failure ADR 0005 is written against.
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
        return ExecResult(exit_code=1, stdout="", stderr="assert 1 == 2", duration_ms=1)


BROKER = EnvCredentialBroker({"KUWARDEN_SCM_TOKEN": "gh-t"})
REPO = RepoRef(host="github.com", org="acme", repo="payments-service")
COMMIT = "c0ffee1234"

#: Fast enough that a test asserting a timeout does not have to wait for one.
SETTINGS = CiConfig(provider="github_actions", wait_s=30, poll_s=5, grace_s=10)


def _run(**overrides: object) -> CiRun:
    fields: dict[str, object] = {
        "id": "1",
        "name": "CI",
        "workflow": ".github/workflows/ci.yml",
        "url": "https://github.com/acme/payments-service/actions/runs/1",
        "head_sha": COMMIT,
        "passed": True,
        "raw_conclusion": "success",
    }
    fields.update(overrides)
    return CiRun(**fields)  # type: ignore[arg-type]


class _Adapter:
    """Returns a scripted sequence of listings, one per poll."""

    def __init__(self, *listings: list[CiRun]) -> None:
        self._listings = list(listings)
        self.calls = 0

    async def runs_for(self, ref: RepoRef, commit: str) -> list[CiRun]:
        index = min(self.calls, len(self._listings) - 1)
        self.calls += 1
        return self._listings[index]


async def _no_sleep(seconds: float) -> None:
    """The wait loop's clock, replaced. The bound under test is the count, not the duration."""


# --- the aggregation rules ----------------------------------------------------------------


async def test_every_run_must_pass() -> None:
    """One green workflow among three does not make a change green."""
    outcome = await await_verdict(
        _Adapter([_run(name="CI"), _run(name="Lint", passed=False, raw_conclusion="failure")]),
        REPO,
        COMMIT,
        SETTINGS,
        sleep=_no_sleep,
    )
    assert outcome.passed is False
    assert "Lint (failure)" in outcome.detail


async def test_all_passing_is_a_pass() -> None:
    outcome = await await_verdict(
        _Adapter([_run(name="CI"), _run(name="Lint")]), REPO, COMMIT, SETTINGS, sleep=_no_sleep
    )
    assert outcome.passed is True
    assert "CI" in outcome.detail
    assert "Lint" in outcome.detail


async def test_a_verdict_for_another_commit_is_discarded() -> None:
    """The precise failure invariant 3 exists to prevent.

    A pass belonging to the previous attempt is not evidence about this one. The platform is
    asked to filter by `head_sha` and is not trusted to have done it.
    """
    outcome = await await_verdict(
        _Adapter([_run(head_sha="an-older-attempt")]), REPO, COMMIT, SETTINGS, sleep=_no_sleep
    )
    assert outcome.passed is None, "a run for another commit must not produce a verdict"
    assert "no pipeline run appeared" in outcome.detail


async def test_a_repository_with_no_pipeline_yields_no_verdict() -> None:
    """Absence is never a pass. It is also never a failure — it is an absence, with a reason."""
    adapter = _Adapter([])
    outcome = await await_verdict(adapter, REPO, COMMIT, SETTINGS, sleep=_no_sleep)

    assert outcome.passed is None
    assert f"within {SETTINGS.grace_s}s" in outcome.detail
    # Polled until the grace period, rather than concluding on the first empty answer: a
    # pipeline takes seconds to be created after a push.
    assert adapter.calls > 1


async def test_a_pipeline_that_appears_late_is_still_read() -> None:
    """The grace period exists for exactly this."""
    adapter = _Adapter([], [], [_run()])
    outcome = await await_verdict(adapter, REPO, COMMIT, SETTINGS, sleep=_no_sleep)

    assert outcome.passed is True


async def test_a_run_still_going_when_the_wait_expires_yields_no_verdict() -> None:
    """Not a pass, and not a failure. The sandbox verdict stands and the reason travels."""
    outcome = await await_verdict(
        _Adapter([_run(passed=None, raw_conclusion="in_progress")]),
        REPO,
        COMMIT,
        SETTINGS,
        sleep=_no_sleep,
    )
    assert outcome.passed is None
    assert "still running" in outcome.detail


async def test_a_pending_run_is_awaited_rather_than_judged() -> None:
    adapter = _Adapter([_run(passed=None, raw_conclusion="queued")], [_run()])
    outcome = await await_verdict(adapter, REPO, COMMIT, SETTINGS, sleep=_no_sleep)

    assert outcome.passed is True
    assert adapter.calls == 2


async def test_only_the_named_workflows_gate() -> None:
    """An application that names its gating workflows is not blocked by a nightly job."""
    settings = CiConfig(provider="github_actions", wait_s=30, poll_s=5, grace_s=10,
                        required_workflows=["CI"])
    outcome = await await_verdict(
        _Adapter([_run(name="CI"), _run(name="Nightly", passed=False, raw_conclusion="failure")]),
        REPO,
        COMMIT,
        settings,
        sleep=_no_sleep,
    )
    assert outcome.passed is True


async def test_a_named_workflow_matches_its_path_too() -> None:
    settings = CiConfig(
        provider="github_actions", required_workflows=[".github/workflows/ci.yml"]
    )
    outcome = await await_verdict(
        _Adapter([_run(name="CI"), _run(name="Nightly", workflow=".github/workflows/n.yml")]),
        REPO,
        COMMIT,
        settings,
        sleep=_no_sleep,
    )
    assert outcome.passed is True
    assert [run.name for run in outcome.runs] == ["CI"]


# --- the GitHub Actions adapter -----------------------------------------------------------


def _transport(payload: object, seen: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=payload)

    return httpx.MockTransport(handler)


async def test_runs_are_requested_for_one_commit() -> None:
    seen: list[httpx.Request] = []
    ci = GitHubActionsCi(BROKER, transport=_transport({"workflow_runs": []}, seen))
    await ci.runs_for(REPO, COMMIT)

    assert seen[0].url.path == "/repos/acme/payments-service/actions/runs"
    assert seen[0].url.params["head_sha"] == COMMIT
    assert seen[0].headers["Authorization"] == "Bearer gh-t"


@pytest.mark.parametrize(
    ("conclusion", "passed"),
    [
        ("success", True),
        # Nothing to do is not a failure.
        ("skipped", True),
        ("neutral", True),
        ("failure", False),
        ("timed_out", False),
        # A run somebody stopped is not evidence that anything succeeded — otherwise a change
        # could be let through by interrupting its own check.
        ("cancelled", False),
        ("action_required", False),
        ("startup_failure", False),
    ],
)
async def test_github_conclusions_are_normalised(conclusion: str, passed: bool) -> None:
    ci = GitHubActionsCi(
        BROKER,
        transport=_transport(
            {
                "workflow_runs": [
                    {
                        "id": 1,
                        "name": "CI",
                        "path": ".github/workflows/ci.yml",
                        "head_sha": COMMIT,
                        "status": "completed",
                        "conclusion": conclusion,
                    }
                ]
            },
            [],
        ),
    )
    runs = await ci.runs_for(REPO, COMMIT)
    assert runs[0].passed is passed
    assert runs[0].raw_conclusion == conclusion, "the platform's own word is kept for diagnosis"


async def test_an_unfinished_run_is_pending_not_failed() -> None:
    """`conclusion` is null while a run is going. Reading it alone turns "running" into "no"."""
    ci = GitHubActionsCi(
        BROKER,
        transport=_transport(
            {
                "workflow_runs": [
                    {
                        "id": 1,
                        "name": "CI",
                        "head_sha": COMMIT,
                        "status": "in_progress",
                        "conclusion": None,
                    }
                ]
            },
            [],
        ),
    )
    runs = await ci.runs_for(REPO, COMMIT)
    assert runs[0].passed is None
    assert runs[0].raw_conclusion == "in_progress"


async def test_a_payload_without_a_run_list_is_an_error_not_an_empty_verdict() -> None:
    """An unparseable answer must not be quietly read as "this repository has no CI"."""
    ci = GitHubActionsCi(BROKER, transport=_transport({"unexpected": True}, []))
    with pytest.raises(AdapterError, match="carried no run list"):
        await ci.runs_for(REPO, COMMIT)


# --- configuration ------------------------------------------------------------------------


def test_an_absent_ci_section_is_legal() -> None:
    """A repository with no pipeline is a repository KuWarden still runs on, with a caveat."""
    text = KUWARDEN_YAML.split("\nci:\n")[0]
    assert parse(text).ci is None


def test_an_unknown_ci_provider_is_refused() -> None:
    with pytest.raises(ConfigError, match="ci.provider"):
        parse(KUWARDEN_YAML.replace("provider: github_actions", "provider: jenkins"))


def test_a_zero_poll_interval_is_refused() -> None:
    """It would spin against someone else's API without ever advancing the elapsed counter."""
    with pytest.raises(ConfigError, match="poll_s"):
        parse(KUWARDEN_YAML.replace("poll_s: 1", "poll_s: 0"))


# --- Build & Test reading the verdict -----------------------------------------------------


def _state() -> FlowState:
    return FlowState(
        run_id=uuid.uuid4(),
        root_run_id=uuid.uuid4(),
        ticket=Ticket(id="PAY-1", system="jira", title="add a helper", body="."),
        policy_commit="0" * 40,
        policy_bundle={},
        branch="kuwarden/pay-1-deadbeef",
        base_branch="main",
        base_commit="base000",
        head_commit="commit-1",
        proposed_edits=[ProposedEdit(path="src/app.py", content="x = 1\n")],
        diff=Diff(files=[FileChange(path="src/app.py", added=1, removed=0)]),
    )


async def _build_test(state: FlowState) -> FlowState:
    with bound(RUNTIME.context()):
        return await NODES["build_test"](state)


async def test_a_passing_pipeline_becomes_the_authoritative_verdict(
    platform: FakePlatform,
) -> None:
    """The whole point: `source` is `ci`, so the evidence document drops its caveat."""
    state = await _build_test(_state())

    assert state.ci_result is not None
    assert state.ci_result.source == "ci"
    assert state.ci_result.is_external_anchor
    assert state.ci_result.exit_code == 0
    # The sandbox verdict is kept, not overwritten. Where the two disagree that is a finding.
    assert state.sandbox_result is not None
    assert state.sandbox_result.source == "sandbox"
    assert "passed: CI" in str(state.ci_detail)


async def test_a_failing_pipeline_fails_the_attempt_even_though_the_sandbox_passed(
    platform: FakePlatform,
) -> None:
    """This is invariant 3 doing something rather than being described."""
    platform.ci_conclusion = "failure"
    state = await _build_test(_state())

    assert state.ci_result is not None
    assert state.ci_result.exit_code != 0, "the flow retries on this"
    assert state.ci_result.source == "ci"
    assert state.sandbox_result is not None
    assert state.sandbox_result.exit_code == 0


async def test_no_pipeline_leaves_the_sandbox_verdict_and_says_why(
    platform: FakePlatform,
) -> None:
    """A missing check must never be indistinguishable from a passing one."""
    platform.ci_has_pipeline = False
    state = await _build_test(_state())

    assert state.ci_result is not None
    assert state.ci_result.source == "sandbox", "absence is not promoted to an anchor"
    assert not state.ci_result.is_external_anchor
    assert "no pipeline run appeared" in str(state.ci_detail)


async def test_a_failing_sandbox_is_not_waited_on(
    platform: FakePlatform, app_config: AppConfig
) -> None:
    """A gate only ever opens on a pass, so only the pass needs an external anchor.

    Waiting up to `wait_s` for CI to confirm a failure the sandbox already found delays the
    Coder's next attempt and changes nothing about the outcome.
    """
    RUNTIME.configure(
        app_config,
        broker=EnvCredentialBroker({"KUWARDEN_SCM_TOKEN": "gh-t"}),
        transport=platform.transport(),
        sandbox=FailingSandbox(),
    )
    state = await _build_test(_state())

    assert state.ci_result is not None
    assert state.ci_result.exit_code == 1
    assert state.ci_result.source == "sandbox"
    assert not [r for r in platform.requests if "/actions/runs" in r.url.path]


async def test_an_empty_change_is_not_anchored_either(platform: FakePlatform) -> None:
    """Nothing executable means nothing for a pipeline to have graded."""
    state = _state()
    state.proposed_edits = []
    state = await _build_test(state)

    assert state.ci_result is not None
    assert state.ci_result.source == "sandbox"
    assert not [r for r in platform.requests if "/actions/runs" in r.url.path]
