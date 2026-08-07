"""⑦ Release — `deterministic`, no LLM.

The control point. Its mechanism is set by `integration_model`, because where KuWarden can
still refuse depends on who performs the deployment — ADR 0004. Under all three models the
Coder holds none of these permissions.

Whatever this node records, it records honestly: `authorized` means KuWarden gated it,
`observed` means we watched it happen. Never inferred, never defaulted.
"""

from __future__ import annotations

from engine.nodes.base import node
from engine.state import FlowState, NodeClass


@node(node_id="release", name="Release", node_class=NodeClass.DETERMINISTIC)
async def release(state: FlowState) -> FlowState:
    return state
