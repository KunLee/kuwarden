"""Push — `deterministic`, no LLM.

Sits between ③ Coder and ④ Build & Test. It is the first half of what ⑦ Release used to do,
split out by [ADR 0007](../../docs/adr/0007-push-before-verification.md) for one reason: **CI
cannot run on a branch that does not exist**. While the push happened at Release, the
project's own pipeline only ever saw a change KuWarden had already finished deciding about,
so no CI adapter could produce the external anchor invariant 3 asks for.

What this node is careful about, and why:

**It holds an SCM branch-write credential and no other.** No merge, no CI trigger, no deploy —
those are not on the `ScmAdapter` interface at all. The Coder, which is the node with a model
in it, holds nothing (invariant 2).

**`protected_paths` is enforced here, before the change leaves the building.** Build & Test
checks the same rule again, but this is the earlier of the two and the one that matters now:
a CI definition that reaches origin is executable *there*, whatever KuWarden decides
afterwards. Both call the same function; neither owns a copy of the rule.

**It pushes a branch. It does not open a pull request.** The pull request is a request
addressed to a human, and it is still made at Release, after verification and after the gate.
Pushing early moves code; it does not move the control point.
"""

from __future__ import annotations

from engine.adapters.factory import scm_adapter
from engine.adapters.protocols import BranchRef, FileEdit
from engine.errors import AdapterError
from engine.nodes.base import context, node
from engine.policy.protected_paths import assert_not_protected
from engine.state import Artifact, FlowState, NodeClass


@node(node_id="push", name="Push", node_class=NodeClass.DETERMINISTIC)
async def push(state: FlowState) -> FlowState:
    ctx = context()
    if not state.proposed_edits:
        raise AdapterError("push reached with no proposed edits")
    if not state.branch or not state.base_branch or not state.base_commit:
        raise AdapterError("push reached before the Coder pinned a branch and a base commit")

    # Invariant 10. The diff comes from git in the sandbox, not from the model's account of
    # what it changed, so an agent that edits a workflow file and then denies it is caught.
    if state.diff is not None:
        assert_not_protected(state.diff.paths)

    repo = ctx.config.primary
    scm = scm_adapter(repo, ctx.broker, transport=ctx.transport)
    ref = repo.ref()

    pushed = await scm.push_change(
        ref,
        # Always the pinned base, so every attempt's tree is base + that attempt's edits.
        BranchRef(name=state.base_branch, commit=state.base_commit),
        branch=state.branch,
        message=_commit_message(state),
        edits=[FileEdit(path=e.path, content=e.content) for e in state.proposed_edits],
        # ...but parented on what this run already pushed, so the branch reads as a history of
        # attempts rather than one commit that quietly replaced another.
        parent=state.head_commit,
    )

    state.head_commit = pushed.commit
    state.artifacts = [
        *state.artifacts,
        Artifact(kind="commit", uri=f"{ref.org}/{ref.repo}@{pushed.commit}", digest=pushed.commit),
    ]
    return state


def _commit_message(state: FlowState) -> str:
    """Trailers carry the run identity into the artefact itself — ADR 0003 §7.

    Backward resolution — *this revision is in production, where did it come from* —
    otherwise depends on correlating timestamps, which fails exactly when it is needed most.

    Two of these trailers are load-bearing beyond documentation:

    `kuwarden-attempt` makes the message unique per (run, attempt), which is what the SCM
    adapters use as the idempotency key. Without it, two attempts of the same run produce the
    same message and the second push would be mistaken for a retry of the first.

    `kuwarden-risk-tier-at-push` is named for when it is read, not for what it settles. Final
    tiering happens after the Coder loop, so the tier at push time is provisional; calling it
    `kuwarden-risk-tier` would put a number in a permanent artefact that disagrees with the
    run record and the pull request for no visible reason.
    """
    return "\n".join(
        [
            f"{state.ticket.id}: {state.ticket.title}"[:72],
            "",
            f"kuwarden-run-id: {state.run_id}",
            f"kuwarden-root-run-id: {state.root_run_id}",
            f"kuwarden-policy-commit: {state.policy_commit}",
            f"kuwarden-risk-tier-at-push: {state.risk_tier}",
            f"kuwarden-attempt: {state.retry_count}",
        ]
    )
