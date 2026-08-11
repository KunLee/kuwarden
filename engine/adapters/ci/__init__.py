"""The project's own pipeline, read back as a verdict.

This is the **independent anchor** invariant 3 asks for. Build & Test's sandbox is ours: the
same system produces the change and grades it, which is evidence of a kind but not of that
kind. A pipeline the organisation already trusts, running in an environment KuWarden does not
control, is a different witness — and until ADR 0007 moved the push inside the loop there was
no branch for it to run on.

Three rules live here rather than in each provider, because a second copy of any of them would
be a second definition of what "the tests passed" means:

**A verdict is only ever read for one commit.** Runs whose `head_sha` is not the commit under
review are discarded, even if the platform returned them. A pass belonging to the previous
attempt is not evidence about this one, and reading it as such is the precise failure invariant
3 exists to prevent.

**Every run must pass.** One green workflow among three does not make a change green.

**Absence is never a pass.** No pipeline, a pipeline that never started, a pipeline still
running when the wait expired — each returns *no verdict*, with a reason. What must not happen
is a missing check becoming a passing one; the caller keeps the sandbox verdict and the reason
travels with it into the evidence document.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from engine.adapters.protocols import RepoRef

if TYPE_CHECKING:  # pragma: no cover - config imports adapters, so this would cycle
    from engine.config import CiConfig


@dataclass(frozen=True)
class CiRun:
    """One pipeline execution, normalised across platforms.

    `passed` is `None` while the run has not finished. `raw_conclusion` keeps whatever the
    platform actually called it: the normalisation is a judgement, and a reader diagnosing a
    surprising verdict needs the unjudged value.
    """

    id: str
    #: The workflow's display name.
    name: str
    #: The workflow's definition path, e.g. `.github/workflows/ci.yml`.
    workflow: str
    url: str
    head_sha: str
    passed: bool | None
    raw_conclusion: str


class CiAdapter(Protocol):
    """Read-only. There is deliberately no `trigger` and no `rerun`.

    A pipeline KuWarden can start is a pipeline KuWarden can influence, and the value of this
    interface is that it cannot. The push is what causes CI to run; this only observes the
    result.
    """

    async def runs_for(self, ref: RepoRef, commit: str) -> list[CiRun]:
        """Every pipeline execution for exactly `commit`."""
        ...


@dataclass(frozen=True)
class CiOutcome:
    """The result of awaiting a pipeline.

    `passed` is `None` when no verdict is available — which is a normal outcome, not an error.
    `detail` is populated in every case, including success, because it is what the audit event
    and the approver's caveat are written from.
    """

    passed: bool | None
    detail: str
    url: str | None = None
    runs: list[CiRun] = field(default_factory=list)


async def await_verdict(
    adapter: CiAdapter,
    ref: RepoRef,
    commit: str,
    settings: CiConfig,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> CiOutcome:
    """Poll until every pipeline for `commit` has finished, or the wait runs out.

    `sleep` is injected so a test can drive the loop without spending the wall clock it
    describes. Elapsed time is accumulated from `poll_s` rather than read from a clock: this
    runs inside an activity, where a clock would be legal, but accumulating makes the bound
    exact and the test deterministic.

    Two separate bounds, because two different things go wrong. `grace_s` covers *nothing has
    appeared yet* — a pipeline takes seconds to be created after a push, and concluding "this
    repository has no CI" in that window would be wrong on every repository that does.
    `wait_s` covers *it appeared and is still going*.
    """
    elapsed = 0
    while True:
        runs = _for_commit(await adapter.runs_for(ref, commit), commit)
        runs = _required(runs, settings.required_workflows)

        if runs:
            pending = [run for run in runs if run.passed is None]
            if not pending:
                return _decide(runs)
            if elapsed >= settings.wait_s:
                names = ", ".join(sorted(run.name for run in pending))
                return CiOutcome(
                    None,
                    f"still running after {settings.wait_s}s: {names}",
                    runs=runs,
                )
        elif elapsed >= settings.grace_s:
            return CiOutcome(
                None,
                f"no pipeline run appeared for {commit[:8]} within {settings.grace_s}s",
            )
        elif elapsed >= settings.wait_s:
            # Only reachable when wait_s < grace_s, which is a misconfiguration rather than a
            # state. Returning beats looping past the bound the operator actually set.
            return CiOutcome(None, f"no pipeline run appeared for {commit[:8]}")

        await sleep(settings.poll_s)
        elapsed += settings.poll_s


def _for_commit(runs: list[CiRun], commit: str) -> list[CiRun]:
    """Discard anything that is not about this exact revision.

    The platform is asked to filter and is not trusted to have done it. A verdict belonging to
    another commit — the previous attempt, a colleague's push to the same branch — would be a
    reality anchor pointing at the wrong reality, which is worse than having none.
    """
    return [run for run in runs if run.head_sha == commit]


def _required(runs: list[CiRun], required: list[str]) -> list[CiRun]:
    """Narrow to the workflows that gate, when the application named any.

    Empty means every workflow counts. A name matches either the display name or the
    definition path, both exactly — the two are what people mean by "the CI workflow", and
    guessing between them with a substring match would silently widen or narrow a gate.
    """
    if not required:
        return runs
    wanted = set(required)
    return [run for run in runs if run.name in wanted or run.workflow in wanted]


def _decide(runs: list[CiRun]) -> CiOutcome:
    """Every run must pass. One green workflow among three does not make a change green."""
    failed = [run for run in runs if not run.passed]
    if failed:
        detail = ", ".join(
            f"{run.name} ({run.raw_conclusion})" for run in sorted(failed, key=_name)
        )
        return CiOutcome(False, f"failed: {detail}", url=failed[0].url, runs=runs)
    names = ", ".join(run.name for run in sorted(runs, key=_name))
    return CiOutcome(True, f"passed: {names}", url=runs[0].url, runs=runs)


def _name(run: CiRun) -> str:
    return run.name
