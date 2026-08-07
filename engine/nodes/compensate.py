"""⑧ Abort / Rollback / Cleanup — `deterministic`.

Compensation is driven from outside the thing that crashed, because a crashed process cannot
clean up after itself: the branch is orphaned, the ticket is stuck in progress, a partial
deployment is unrolled back.
"""

from __future__ import annotations

from engine.nodes.base import node
from engine.state import FlowState, NodeClass


@node(node_id="compensate", name="Abort / Rollback / Cleanup", node_class=NodeClass.DETERMINISTIC)
async def compensate(state: FlowState) -> FlowState:
    return state
