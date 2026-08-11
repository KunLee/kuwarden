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

from engine.adapters.factory import scm_adapter
from engine.errors import AdapterError
from engine.nodes.base import context, node
from engine.state import Artifact, FlowState, NodeClass


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

    pull_request = await scm.open_pull_request(
        ref,
        source=state.branch,
        # The branch pinned at the Coder, not whatever `default_branch` answers now. A default
        # branch renamed mid-run must not silently retarget the pull request.
        target=state.base_branch,
        title=f"{state.ticket.id}: {state.ticket.title}"[:200],
        description=_description(state),
    )

    state.artifacts = [
        *state.artifacts,
        Artifact(kind="pull_request", uri=pull_request.url, digest=pull_request.id),
    ]
    return state


def _description(state: FlowState) -> str:
    verdicts = "\n".join(
        f"- {v.verifier}: {'passed' if v.passed else 'FAILED'}" for v in state.verifications
    ) or "- none recorded"
    approvals = "\n".join(
        f"- {a.principal}: {'approved' if a.approved else 'rejected'} "
        f"(evidence {a.evidence_digest})"
        for a in state.approvals
    ) or "- none required at this risk tier"

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
