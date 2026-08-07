"""③ Coder — `generative`, inside the sandbox.

Holds a bounded inner loop: act, build, read the failure, fix. Nearly all of a coding
agent's quality comes from this cycle rather than from one-shot generation. The loop is
contained *inside* the node, where its context is bounded and its retries are budgeted; the
flow between nodes stays deterministic.

Produces a diff. It never pushes — that happens outside, under a different identity.
"""

from __future__ import annotations

from engine.nodes.base import node
from engine.state import Diff, FlowState, NodeClass


@node(node_id="coder", name="Coder", node_class=NodeClass.GENERATIVE)
async def coder(state: FlowState) -> FlowState:
    state.diff = Diff(files=[])
    return state
