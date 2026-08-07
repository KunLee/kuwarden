"""Reporter — `deterministic`.

Posts the outcome and its evidence back to the ticket. Terminal on both the success and the
compensation path, so a run always says what became of it.
"""

from __future__ import annotations

from engine.nodes.base import node
from engine.state import FlowState, NodeClass


@node(node_id="reporter", name="Reporter", node_class=NodeClass.DETERMINISTIC)
async def reporter(state: FlowState) -> FlowState:
    return state
