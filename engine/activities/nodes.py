"""Node execution as an activity.

Nodes have side effects and hold the only LLM calls in the system, so they are activities,
never workflow code. This module is the seam: `flows/` imports from here and never from
`nodes/`, which keeps the determinism boundary visible in the import graph rather than
resting on everyone remembering it.
"""

from __future__ import annotations

from dataclasses import dataclass

from temporalio import activity

from engine.nodes import NODES
from engine.state import FlowState


@dataclass
class NodeInput:
    node_id: str
    state: FlowState


@activity.defn
async def run_node(params: NodeInput) -> FlowState:
    fn = NODES.get(params.node_id)
    if fn is None:
        raise LookupError(f"unknown node {params.node_id!r}")
    return await fn(params.state)
