"""④ Build & Test — `deterministic`, no LLM.

Emits the objective verdict. The exit code is the anchor; an agent's assertion that its work
succeeded is never a gate input — ADR 0001.

Two things happen here that are easy to conflate and must not be:

**`protected_paths` is checked against the diff, not the agent's account of it.** The file
list comes from git in the workspace, so an agent that edits a CI definition and then reports
that it did not is caught anyway — ADR 0004 §1.

**Two graders, and which one counts is recorded.** The sandbox runs first: it is fast, it is
what drives the Coder's inner loop, and it is *ours* — the same system produced the change and
graded it, so it is not the external system of record invariant 3 asks for. When the
application declares a `ci` section, a passing sandbox run is then anchored against the
project's own pipeline for the pushed commit, and that verdict takes over with `source="ci"`.

Both travel: `sandbox_result` is kept alongside `ci_result`, and `ci_detail` says how the
verdict was reached or why no CI verdict exists. A missing check must never be
indistinguishable from a passing one — with no `ci` section, or no pipeline, or a pipeline
still running when the wait expired, the sandbox verdict stands and carries its caveat.

**A sandbox failure is not a code failure.** Three outcomes, three responses (ADR 0005 §5):
a non-zero exit means the code is wrong and the Coder gets another attempt; a limit hit means
the change may be pathological or the bounds too tight, and it is surfaced explicitly; an
infrastructure error means the sandbox itself broke and **must not consume a retry**, because
retrying spends budget on something the Coder cannot fix.
"""

from __future__ import annotations

from engine.adapters.ci import await_verdict
from engine.adapters.factory import ci_adapter
from engine.errors import SandboxInfrastructureError
from engine.nodes.base import NodeContext, context, node
from engine.policy.protected_paths import assert_not_protected
from engine.sandbox import ResourceLimits
from engine.sandbox.workspace import materialise
from engine.state import CIResult, FlowState, NodeClass


@node(node_id="build_test", name="Build & Test", node_class=NodeClass.DETERMINISTIC)
async def build_test(state: FlowState) -> FlowState:
    ctx = context()

    # Enforced before anything is executed. A diff that touches a CI definition is refused
    # rather than run — the escalation path ADR 0004 closes is "agent writes a workflow file,
    # workflow file executes with CI credentials", and running the tests first would already
    # be too late in a real pipeline.
    #
    # Push checks this first and is now the deciding one, since the file reaches origin there.
    # Kept here as well because the two nodes are separable: a topology that ever executes a
    # diff without pushing it must still refuse this one.
    if state.diff is not None:
        assert_not_protected(state.diff.paths)

    if not state.proposed_edits or ctx.sandbox is None:
        # Nothing executable to build. Recorded as a pass rather than invented as a failure:
        # a fabricated verdict is exactly what the reality-anchor rule exists to prevent.
        state.sandbox_result = CIResult(exit_code=0, source="sandbox")
        state.ci_result = state.sandbox_result
        return state

    # The workspace is rebuilt here rather than shared with the Coder.
    #
    # Coder and Build & Test are separate Temporal activities, and Temporal is free to run
    # them on different workers — a host temp directory would simply not be there. So the
    # workspace is reconstructed from state that travelled through workflow history, which
    # also means a replay produces the same tree as the original run.
    settings = ctx.config.sandbox
    files = {edit.path: edit.content for edit in state.proposed_edits}

    try:
        async with materialise(files) as workspace:
            result = await ctx.sandbox.exec(
                workspace,
                settings.toolchain_image,
                settings.test_command,
                ResourceLimits(
                    memory_mb=settings.memory_mb,
                    cpus=settings.cpus,
                    pids=settings.pids,
                    timeout_s=settings.timeout_s,
                    tmp_mb=settings.tmp_mb,
                ),
            )
    except SandboxInfrastructureError:
        # Re-raised rather than converted into a failing CIResult. The flow treats this as
        # non-retryable and routes to compensation; charging the Coder a retry for our own
        # infrastructure teaches it the wrong lesson and spends budget it cannot recover.
        raise

    # Taken from the sandbox's own probe rather than from configuration. Configuration says
    # what was asked for; `enforced` says what the host applied, and only the second is
    # evidence.
    if result.enforced is not None:
        state.sandbox_isolation = "enforced" if result.enforced.fully_enforced else "degraded"
        state.sandbox_gaps = result.enforced.gaps()

    state.sandbox_result = CIResult(
        exit_code=result.exit_code,
        duration_ms=result.duration_ms,
        # Ours, not the project's pipeline. See the module docstring: this node is a fast
        # inner-loop anchor, and the gate must not read it as an independent one.
        source="sandbox",
    )
    state.ci_result = state.sandbox_result

    # Only when the sandbox passed.
    #
    # This asymmetry is the point rather than an optimisation. A gate only ever opens on a
    # pass, so the pass is the claim that has to be anchored externally; a failure already
    # sends the Coder round again, and spending fifteen minutes waiting for CI to agree buys
    # nothing and delays the retry.
    if result.exit_code == 0:
        await _anchor_to_ci(ctx, state)
    return state


async def _anchor_to_ci(ctx: NodeContext, state: FlowState) -> None:
    """Replace the sandbox verdict with the project's own pipeline, if it produced one.

    Mutates `state` rather than returning it: this is one step of the node's work, not a node
    in its own right, and the uniform `(FlowState) -> FlowState` signature belongs to nodes.

    Absence of a verdict is never converted into a pass. When CI has nothing to say, the
    sandbox result stands and `ci_detail` records why — which becomes the caveat an approver
    reads. Inventing a pass, or failing a run because someone's repository has no pipeline,
    would each be answering a question nobody asked.
    """
    if ctx.config.ci is None:
        return
    if not state.head_commit:
        state.ci_detail = "nothing was pushed, so there is no commit for a pipeline to run on"
        return

    adapter = ci_adapter(ctx.config.ci, ctx.broker, transport=ctx.transport)
    outcome = await await_verdict(
        adapter, ctx.config.primary.ref(), state.head_commit, ctx.config.ci
    )
    state.ci_detail = outcome.detail
    if outcome.passed is None:
        return

    state.ci_result = CIResult(
        exit_code=0 if outcome.passed else 1,
        # The independent anchor invariant 3 asks for: a pipeline the organisation already
        # trusts, in an environment KuWarden does not control.
        source="ci",
        url=outcome.url,
    )
