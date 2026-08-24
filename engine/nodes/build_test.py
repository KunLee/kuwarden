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
from engine.adapters.factory import ci_adapter, scm_adapter
from engine.errors import SandboxInfrastructureError
from engine.nodes import notes
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
        state.notes = notes.compose(
            "Nothing was executed — there was nothing executable",
            notes.fields(
                "Why no verdict was produced",
                [
                    ("Proposed edits", len(state.proposed_edits)),
                    ("Sandbox", "configured" if ctx.sandbox else "none configured"),
                    ("Recorded as", "exit 0, source sandbox"),
                    # The distinction this whole node exists to preserve, at the one point it
                    # is easiest to lose.
                    ("Meaning", "no tests ran; this is not evidence that any test passed"),
                ],
            ),
        )
        return state

    # The workspace is rebuilt here rather than shared with the Coder.
    #
    # Coder and Build & Test are separate Temporal activities, and Temporal is free to run
    # them on different workers — a host temp directory would simply not be there. So the
    # workspace is reconstructed, and it must be reconstructed *whole*.
    #
    # It used to be built from `proposed_edits` alone — the changed files and nothing else.
    # That survived undetected for as long as `test_command` was `pytest -q` against a
    # repository with no tests, because collecting nothing succeeds whatever the directory
    # holds. The moment the sandbox ran a real toolchain it failed instantly and for the
    # wrong reason: three files in an empty directory, and eslint exiting 2 with "couldn't
    # find eslint.config.js" — which the flow reads as *the change is broken* and sends back
    # to the Coder, who cannot possibly fix it.
    #
    # So the tree is re-read at the commit the Coder pinned, and the edits are laid over it.
    # Pinned, not re-resolved: the same commit the Coder worked against, so a branch moving
    # under the run cannot change what is graded, and a replay produces the same tree.
    settings = ctx.config.sandbox
    if not state.base_commit:
        raise SandboxInfrastructureError(
            "build_test reached without a pinned base commit; the Coder sets it and every "
            "grading run must execute against the tree the change was written for"
        )
    repo = ctx.config.primary
    scm = scm_adapter(repo, ctx.broker, transport=ctx.transport)
    tree = await scm.read_tree(repo.ref(), state.base_commit)

    files: dict[str, str | bytes] = dict(tree.files)
    for edit in state.proposed_edits:
        if edit.deleted:
            # Removed rather than written empty. A deleted path recreated as an empty file is
            # a different change, and the suite would be exercising something the diff took
            # away — while still resolving every import of it.
            files.pop(edit.path, None)
        else:
            files[edit.path] = edit.content

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

    anchored = state.ci_result is not None and state.ci_result.is_external_anchor
    state.notes = notes.compose(
        f"Tests exited {result.exit_code} in the sandbox — "
        + (
            "anchored against the project's own pipeline"
            if anchored
            else "not anchored against an external pipeline"
        ),
        notes.fields(
            "Sandbox run — ours, and therefore not an independent witness",
            [
                ("Command", " ".join(settings.test_command)),
                ("Image", settings.toolchain_image),
                ("Exit code", result.exit_code),
                ("Duration", f"{result.duration_ms} ms"),
                ("Limits hit", result.limits_hit or "none"),
                ("Files materialised", len(files)),
                ("Isolation", state.sandbox_isolation or "not probed"),
                ("Isolation gaps", state.sandbox_gaps or "none"),
            ],
        ),
        notes.fields(
            "Invariant 3 — was the verdict read from an external system of record?",
            [
                ("CI section declared", "yes" if ctx.config.ci is not None else "no"),
                ("Authoritative verdict", state.ci_result.source if state.ci_result else "none"),
                (
                    "Independent anchor",
                    "yes"
                    if anchored
                    # The caveat is the whole point: a run whose CI was never consulted must
                    # never read later like one whose CI passed.
                    else "no — the sandbox verdict stands, and it is labelled as such",
                ),
                ("Pipeline URL", state.ci_result.url if state.ci_result else None),
                ("Detail", state.ci_detail),
            ],
        ),
        # Capped by `notes.text`. Test output is the single most useful thing here when a run
        # loops, and it is also the single largest thing a node can produce.
        notes.text("Test output — stdout", result.stdout, tail=True) if result.stdout else None,
        notes.text("Test output — stderr", result.stderr, tail=True) if result.stderr else None,
    )
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
