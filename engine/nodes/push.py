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
from engine.nodes import notes
from engine.nodes.base import context, node
from engine.policy.protected_paths import assert_not_protected
from engine.state import Artifact, FlowState, NodeClass


@node(node_id="push", name="Push", node_class=NodeClass.DETERMINISTIC)
async def push(state: FlowState) -> FlowState:
    ctx = context()
    # Tested against the git-computed diff rather than against `proposed_edits`, and the two
    # are not the same question. A removal is an edit: it appears in the diff and, before
    # deletions were carried, contributed no content-bearing entry — so a change that only
    # deleted files was refused here as though the Coder had produced nothing.
    #
    # An empty diff is still a failure, but it belongs to the Coder, and the message says so:
    # "no proposed edits" named the symptom three nodes downstream of the cause and sent a
    # reader looking at Push.
    if state.diff is None or not state.diff.files:
        raise AdapterError(
            "the Coder produced no change — the sandbox tree is identical to the base commit. "
            "Read the Coder's notes for what it was shown and what it decided; a run reaches "
            "here having proposed nothing when the model could not find what the ticket names."
        )
    if not state.branch or not state.base_branch or not state.base_commit:
        raise AdapterError("push reached before the Coder pinned a branch and a base commit")

    # Invariant 10. The diff comes from git in the sandbox, not from the model's account of
    # what it changed, so an agent that edits a workflow file and then denies it is caught.
    if state.diff is not None:
        assert_not_protected(state.diff.paths)

    repo = ctx.config.primary
    scm = scm_adapter(repo, ctx.broker, transport=ctx.transport)
    ref = repo.ref()

    # Read before the push overwrites it. `None` on the first attempt; on a later one it is the
    # previous attempt's commit, which is what makes the branch a history rather than a
    # replacement — and is worth having in the record for exactly that reason.
    parent = state.head_commit
    message = _commit_message(state)

    pushed = await scm.push_change(
        ref,
        # Always the pinned base, so every attempt's tree is base + that attempt's edits.
        BranchRef(name=state.base_branch, commit=state.base_commit),
        branch=state.branch,
        message=message,
        edits=[
            FileEdit(path=e.path, content=e.content, deleted=e.deleted)
            for e in state.proposed_edits
        ],
        # ...but parented on what this run already pushed, so the branch reads as a history of
        # attempts rather than one commit that quietly replaced another.
        parent=parent,
    )

    state.head_commit = pushed.commit
    state.artifacts = [
        *state.artifacts,
        Artifact(kind="commit", uri=f"{ref.org}/{ref.repo}@{pushed.commit}", digest=pushed.commit),
    ]

    state.notes = notes.compose(
        f"Pushed {len(state.proposed_edits)} file(s) to {state.branch} as {pushed.commit[:12]}",
        notes.fields(
            "Where it went",
            [
                ("Repository", f"{ref.org}/{ref.repo}"),
                ("Branch", state.branch),
                ("Base commit, pinned by the Coder", state.base_commit),
                (
                    "Parent",
                    parent or "none — first attempt, so this commit sits on the base",
                ),
                ("New commit", pushed.commit),
                ("Pass of the Coder/Build cycle", state.push_attempt),
                ("Retries inside the Coder", state.retry_count),
            ],
        ),
        notes.checks(
            "Checked before anything reached origin",
            [
                (
                    "Protected paths",
                    "no CI definition, deploy manifest, IaC or KuWarden config",
                    state.diff.paths if state.diff else "no diff to check",
                    True,
                ),
            ],
        ),
        notes.fields(
            "What this node may do",
            [
                # Invariant 2, stated in the record rather than only in the ADR. The reader of
                # a run is entitled to see which permissions were in play at the moment code
                # left the building.
                ("Credential held", "scm.write_branch, and nothing else"),
                ("Merge", "not on the adapter interface"),
                ("Pull request", "not here — Release opens it, after the gate"),
                ("Control point moved", "no — pushing a branch is not one of the three"),
            ],
        ),
        notes.text("Commit message, with the trailers that make the run resolvable", message),
        notes.fields(
            "Files written",
            [
                (
                    edit.path,
                    # A deletion has no line count to report, and "0 lines" would read as an
                    # emptied file rather than a removed one.
                    "deleted"
                    if edit.deleted
                    else f"{len(edit.content.splitlines())} lines",
                )
                for edit in state.proposed_edits
            ],
        ),
    )
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
            # The idempotency key, and it must be the OUTER loop's counter. `retry_count`
            # is the Coder's inner one, which restarts at 0 on every invocation — using it
            # made the second pass produce an identical message, which the adapters read as
            # "this push already landed" and skipped, discarding the work silently.
            f"kuwarden-attempt: {state.push_attempt}",
        ]
    )
