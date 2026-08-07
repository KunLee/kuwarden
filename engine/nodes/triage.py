"""① Triage & Risk Router — `deterministic`, with an advisory LLM.

Stage one of two. The facts final tiering depends on — which paths the diff touches, whether
it reaches `migrations/`, how large it is — do not exist yet, because there is no diff. What
this stage produces is provisional: admission control and budget allocation, and it may be
wrong.

Tiering is rules-first. An advisory model may contribute, but only to raise a tier.
"""

from __future__ import annotations

from engine.nodes.base import node
from engine.state import FlowState, NodeClass


@node(node_id="triage", name="Triage & Risk Router", node_class=NodeClass.DETERMINISTIC)
async def triage(state: FlowState) -> FlowState:
    state.provisional_risk_tier = state.risk_tier
    return state
