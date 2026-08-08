"""③ Coder — `generative`, inside the sandbox.

Holds a bounded inner loop: act, build, read the failure, fix. Nearly all of a coding agent's
quality comes from that cycle rather than from one-shot generation, and the loop is contained
*inside* this node so the flow between nodes stays deterministic.

**None of that exists yet.** There is no sandbox and no model, so what runs here is a
deterministic marker change. It establishes the write path, the protected-path check, the
branch and the pull request against real platforms without a model being involved — which is
the whole point of proving the control plane first. When the sandbox and the LLM adapter
land, this body is what they replace.

The change is produced here. It is never pushed from here.
"""

from __future__ import annotations

from engine.nodes.base import context, node
from engine.state import Diff, FileChange, FlowState, NodeClass, ProposedEdit

MARKER_PATH = "kuwarden-run.md"


@node(node_id="coder", name="Coder", node_class=NodeClass.GENERATIVE)
async def coder(state: FlowState) -> FlowState:
    ctx = context()

    # Deliberately not derived from ticket text. Ticket content is hostile input, and
    # echoing it into a file the flow then commits would make the first real run a
    # demonstration of the injection path rather than of the control plane.
    content = "\n".join(
        [
            f"# {ctx.config.name}",
            "",
            f"- run: `{state.run_id}`",
            f"- ticket: `{state.ticket.system}:{state.ticket.id}`",
            f"- policy: `{state.policy_commit}`",
            f"- attempt: {state.retry_count}",
            "",
            state.plan.summary if state.plan else "The Planner node has no model yet.",
            "",
        ]
    )

    state.branch = state.branch or f"kuwarden/{state.ticket.id.lower()}-{state.run_id.hex[:8]}"
    state.proposed_edits = [ProposedEdit(path=MARKER_PATH, content=content)]
    state.diff = Diff(
        files=[FileChange(path=MARKER_PATH, added=len(content.splitlines()), removed=0)]
    )
    return state
