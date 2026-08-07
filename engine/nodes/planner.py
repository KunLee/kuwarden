"""② Planner — `generative`.

Ticket plus codebase into a structured change plan. The only node whose output legitimately
becomes the next node's input context: `Planner → Coder` hands forward, `Coder → Verifier`
must not.
"""

from __future__ import annotations

from engine.nodes.base import node
from engine.state import ChangePlan, FlowState, NodeClass


@node(node_id="planner", name="Planner", node_class=NodeClass.GENERATIVE)
async def planner(state: FlowState) -> FlowState:
    state.plan = ChangePlan(summary="", steps=[])
    return state
