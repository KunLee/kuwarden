"""⑦ Release — `deterministic`, no LLM.

The control point. Its mechanism is set by `integration_model`, because where KuWarden can
still refuse depends on who performs the deployment — ADR 0004. Under all three models the
Coder holds none of these permissions: this node resolves its own credentials, and it is the
only place a branch is pushed.

What is implemented here is the part common to all three models — push the branch the Coder
produced, and open a pull request. What follows the pull request differs per model and is not
built yet, which is why nothing here records a `control_mode`. Recording one would be
claiming a control we do not yet exert.
"""

from __future__ import annotations

from engine.adapters.factory import scm_adapter
from engine.adapters.protocols import FileEdit
from engine.errors import AdapterError
from engine.nodes.base import context, node
from engine.state import Artifact, FlowState, NodeClass


@node(node_id="release", name="Release", node_class=NodeClass.DETERMINISTIC)
async def release(state: FlowState) -> FlowState:
    ctx = context()
    if not state.proposed_edits:
        raise AdapterError("release reached with no proposed edits")
    if not state.branch:
        raise AdapterError("release reached with no branch name")

    repo = ctx.config.primary
    scm = scm_adapter(repo, ctx.broker, transport=ctx.transport)
    ref = repo.ref()

    base = await scm.default_branch(ref)
    pushed = await scm.push_change(
        ref,
        base,
        branch=state.branch,
        message=_commit_message(state),
        edits=[FileEdit(path=e.path, content=e.content) for e in state.proposed_edits],
    )

    pull_request = await scm.open_pull_request(
        ref,
        source=pushed.name,
        target=base.name,
        title=f"{state.ticket.id}: {state.ticket.title}"[:200],
        description=_description(state),
    )

    state.artifacts = [
        *state.artifacts,
        Artifact(kind="commit", uri=f"{ref.org}/{ref.repo}@{pushed.commit}", digest=pushed.commit),
        Artifact(kind="pull_request", uri=pull_request.url, digest=pull_request.id),
    ]
    return state


def _commit_message(state: FlowState) -> str:
    """Trailers carry the run identity into the artefact itself — ADR 0003 §7.

    Backward resolution — *this revision in production, where did it come from* — otherwise
    depends on correlating timestamps, which fails exactly when it is needed most.
    """
    return "\n".join(
        [
            f"{state.ticket.id}: {state.ticket.title}"[:72],
            "",
            f"kuwarden-run-id: {state.run_id}",
            f"kuwarden-root-run-id: {state.root_run_id}",
            f"kuwarden-policy-commit: {state.policy_commit}",
            f"kuwarden-risk-tier: {state.risk_tier}",
        ]
    )


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
            f"**Risk tier:** {state.risk_tier}",
            f"**Run:** `{state.run_id}`",
            f"**Policy commit:** `{state.policy_commit}`",
            "",
            "### Verification",
            verdicts,
            "",
            "### Approvals",
            approvals,
        ]
    )
