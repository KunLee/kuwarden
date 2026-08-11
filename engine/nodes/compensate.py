"""⑧ Abort / Rollback / Cleanup — `deterministic`.

Compensation is driven from outside the thing that crashed, because a crashed process cannot
clean up after itself: the branch is orphaned, the ticket is stuck in progress, a partial
deployment is unrolled back.

**Deleting is destroying evidence, so this node deletes narrowly.** The branch is removed only
when no pull request was opened against it — if one was, a human is already involved and
removing the branch under them is not tidying, it is taking away the thing they were asked to
look at. Where nothing was opened, nobody outside KuWarden ever saw the branch, and the commit
sha stays in the append-only record either way.

**Nothing here may raise.** This node runs *because* something already went wrong. A failure
during cleanup that propagated would replace a diagnosable original error with a confusing
second one, and the run would end reporting the wrong cause. Every step is attempted, every
failure is recorded on the state, and the node returns.

What is deliberately **not** done: the ticket is not transitioned back. Moving somebody's work
item between states is a governance act, not cleanup — the Reporter comments on it instead,
and a human decides what the ticket should say.
"""

from __future__ import annotations

import logging

from engine.adapters.factory import scm_adapter
from engine.errors import KuWardenError
from engine.nodes.base import context, node
from engine.state import FlowState, NodeClass

log = logging.getLogger(__name__)


@node(node_id="compensate", name="Abort / Rollback / Cleanup", node_class=NodeClass.DETERMINISTIC)
async def compensate(state: FlowState) -> FlowState:
    ctx = context()

    if not state.branch or not state.head_commit:
        # Nothing was pushed, so there is nothing on anyone's remote to tidy.
        return state

    if any(artifact.kind == "pull_request" for artifact in state.artifacts):
        state.cleanup = "branch kept: a pull request was opened against it"
        return state

    repo = ctx.config.primary
    try:
        removed = await scm_adapter(repo, ctx.broker, transport=ctx.transport).delete_branch(
            repo.ref(), state.branch
        )
    except (KuWardenError, Exception) as exc:  # noqa: BLE001 - cleanup never fails the run
        # Recorded rather than raised. A cleanup failure is worth knowing about and is not
        # worth losing the original error over.
        log.warning("run %s: could not delete %s: %s", state.run_id, state.branch, exc)
        state.cleanup = f"branch {state.branch} could not be deleted: {exc}"
        return state

    state.cleanup = (
        f"branch {state.branch} deleted at {state.head_commit[:8]}"
        if removed
        else f"branch {state.branch} was already gone"
    )
    return state
