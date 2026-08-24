"""⑦ Release — `deterministic`, no LLM.

The control point. Its mechanism is set by `integration_model`, because where KuWarden can
still refuse depends on who performs the deployment — ADR 0004. Under all three models the
Coder holds none of these permissions; this node resolves its own credentials.

The branch is no longer pushed here. That moved ahead of Build & Test with
[ADR 0007](../../docs/adr/0007-push-before-verification.md), so that the project's own CI can
run on the change while the run is still able to act on the result. What is left here is the
part that was always the point: **opening the pull request** — a request addressed to a human,
made only after the verifiers have passed and the gate has been satisfied.

What follows the pull request differs per integration model and is not built yet, which is why
nothing here records a `control_mode`. Recording one would be claiming a control we do not yet
exert.
"""

from __future__ import annotations

from dataclasses import dataclass

from engine.adapters.factory import scm_adapter
from engine.adapters.protocols import IntegrationModel
from engine.config import AppConfig
from engine.errors import AdapterError
from engine.nodes import notes
from engine.nodes.base import context, node
from engine.state import RISK_TIER_ORDER, Artifact, FlowState, NodeClass


@node(node_id="release", name="Release", node_class=NodeClass.DETERMINISTIC)
async def release(state: FlowState) -> FlowState:
    ctx = context()
    # Push is what establishes all three. Reaching Release without them means the topology
    # skipped a node, which is a defect rather than a state to recover from — opening a pull
    # request for a branch nobody pushed asks a human to review nothing.
    if not state.branch or not state.head_commit or not state.base_branch:
        raise AdapterError("release reached with no pushed branch")

    repo = ctx.config.primary
    scm = scm_adapter(repo, ctx.broker, transport=ctx.transport)
    ref = repo.ref()

    title = f"{state.ticket.id}: {state.ticket.title}"[:200]
    description = _description(state)
    pull_request = await scm.open_pull_request(
        ref,
        source=state.branch,
        # The branch pinned at the Coder, not whatever `default_branch` answers now. A default
        # branch renamed mid-run must not silently retarget the pull request.
        target=state.base_branch,
        title=title,
        description=description,
    )

    state.artifacts = [
        *state.artifacts,
        Artifact(kind="pull_request", uri=pull_request.url, digest=pull_request.id),
    ]

    # ADR 0004 model B: `gated_merge` is defined as KuWarden *holding merge authority*. This
    # is that authority being exercised, under conditions the application declared in advance.
    # The verdict is computed first and recorded whichever way it goes, so the trail says why
    # a change was left for a human as well as why one was not.
    merge_verdict = _may_merge(ctx.config, state)
    merged_commit: str | None = None
    if merge_verdict.allowed:
        merged_commit = await scm.merge_pull_request(
            ref,
            pull_request.id,
            # The revision the verifiers and the gate actually judged. GitHub refuses with 409
            # if the head has moved since, so a push landing in that window cannot be merged
            # unverified.
            state.head_commit,
        )
        state.artifacts = [
            *state.artifacts,
            Artifact(kind="merge", uri=pull_request.url, digest=merged_commit),
        ]
        # Read by the flow, which emits the `external_effect` row carrying
        # `control_mode="authorized"`. The node does not write the audit trail itself.
        state.merged_commit = merged_commit

    state.notes = notes.compose(
        f"Opened {pull_request.url}"
        + (f" and merged it as {merged_commit[:12]}" if merged_commit else ""),
        notes.fields(
            "The pull request",
            [
                ("URL", pull_request.url),
                ("Source branch", state.branch),
                ("Target branch", state.base_branch),
                ("Head commit", state.head_commit),
                ("Title", title),
            ],
        ),
        notes.fields(
            "What had to be true before this node ran",
            [
                ("Verifiers", f"{len(state.verifications)} ran, all passed"),
                ("Risk tier, final", state.risk_tier),
                (
                    "Approvals",
                    [
                        f"{a.principal} ({'approved' if a.approved else 'rejected'})"
                        for a in state.approvals
                    ]
                    or "none required at this tier",
                ),
            ],
        ),
        notes.fields(
            "Control point",
            [
                ("Integration model", ctx.config.integration_model),
                # Invariant 11: `authorized` is recorded when KuWarden gated the effect, and
                # only then. A merge it performed is exactly that; a pull request left for a
                # human is no effect of KuWarden's at all, so it records nothing rather than
                # something weaker.
                (
                    "control_mode recorded",
                    "authorized — KuWarden merged this itself"
                    if merged_commit
                    else "none — this node opened a request, and performed no effect",
                ),
                ("Auto-merge", merge_verdict.detail),
                (
                    "This node did",
                    f"merge, as {merged_commit}"
                    if merged_commit
                    else "open a request addressed to a human",
                ),
            ],
        ),
        notes.text("Pull request description, as posted", description),
    )
    return state


@dataclass(frozen=True)
class _MergeVerdict:
    """Whether auto-merge applies, and the sentence explaining it either way."""

    allowed: bool
    detail: str


def _may_merge(config: AppConfig, state: FlowState) -> _MergeVerdict:
    """Evaluate the declared auto-merge policy against this run.

    Every clause is checked and the reason is recorded whichever way it goes, because "why was
    this merged" and "why was this left for me" are the same question asked from two sides,
    and an approval queue nobody understands is one people learn to clear without reading.

    Order matters only for the message. The checks are independent and all must hold.
    """
    policy = config.auto_merge
    if not policy.enabled:
        return _MergeVerdict(False, "not enabled for this application")
    if config.integration_model is not IntegrationModel.GATED_MERGE:
        # Belt and braces: the config parser already refuses this combination.
        return _MergeVerdict(
            False, f"integration_model is {config.integration_model.value}, not gated_merge"
        )

    if RISK_TIER_ORDER[state.risk_tier] > RISK_TIER_ORDER[policy.max_risk_tier]:
        return _MergeVerdict(
            False,
            f"risk tier {state.risk_tier} is above the {policy.max_risk_tier} ceiling",
        )

    # An unapproved rejection must never reach here, but a *rejecting* approval among the
    # records means a human said no, and no policy ceiling outranks that.
    if any(not approval.approved for approval in state.approvals):
        return _MergeVerdict(False, "a human rejected this change")

    changed = len(state.diff.files) if state.diff else 0
    if policy.max_files_changed is not None and changed > policy.max_files_changed:
        return _MergeVerdict(
            False,
            f"{changed} files changed, above the {policy.max_files_changed} allowed",
        )

    anchored = state.ci_result is not None and state.ci_result.is_external_anchor
    if policy.require_external_anchor and not anchored:
        # The clause that matters most. Without it a change reaches the default branch graded
        # only by the sandbox that produced it, which is the arrangement this product exists
        # to argue against — see CLAUDE.md, invariant 3.
        return _MergeVerdict(
            False,
            "no external anchor: "
            + (state.ci_detail or "the verdict came from KuWarden's own sandbox"),
        )

    return _MergeVerdict(
        True,
        f"tier {state.risk_tier} at or below {policy.max_risk_tier}, {changed} file(s) changed"
        + (", anchored against the project's own pipeline" if anchored else "")
        + " — merged under the declared policy",
    )


def _description(state: FlowState) -> str:
    verdicts = (
        "\n".join(
            f"- {v.verifier}: {'passed' if v.passed else 'FAILED'}" for v in state.verifications
        )
        or "- none recorded"
    )
    approvals = (
        "\n".join(
            f"- {a.principal}: {'approved' if a.approved else 'rejected'} "
            f"(evidence {a.evidence_digest})"
            for a in state.approvals
        )
        or "- none required at this risk tier"
    )

    return "\n".join(
        [
            f"Raised by KuWarden for {state.ticket.system} {state.ticket.id}.",
            "",
            # The authoritative tier. The commit trailer says `at-push`, which is the
            # provisional one — final tiering runs after the Coder loop.
            f"**Risk tier:** {state.risk_tier}",
            f"**Run:** `{state.run_id}`",
            f"**Head:** `{state.head_commit}`",
            f"**Policy commit:** `{state.policy_commit}`",
            "",
            "### Verification",
            verdicts,
            "",
            "### Approvals",
            approvals,
        ]
    )
