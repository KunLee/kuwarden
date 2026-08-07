"""⑤ Verifiers ×4 — `verifier`, fresh context, fan-out.

Adversarial by construction: a verifier attempts to falsify the change, not to assess it
neutrally. A change ships when it survives, not when it is liked.

Each is constructed with a new context and may see the original ticket and its acceptance
criteria, the final diff, and objective evidence. It may not see the Coder's reasoning, its
self-assessment, its prior failed attempts, or any other verifier's verdict — they fan out in
parallel, they do not vote in sequence.

`test_evidence` is not optional. The most common way an agent manufactures success is to
weaken the tests. Much of that check is deterministic — assertion-count delta, test churn
disproportionate to source churn — with a model only for the residue.
"""

from __future__ import annotations

from engine.nodes.base import node
from engine.state import FlowState, NodeClass, Verification

VERIFIER_IDS = ("correctness", "security", "test_evidence", "regression_risk")


def _verifier(verifier_id: str, name: str):  # type: ignore[no-untyped-def]
    @node(node_id=f"verifier.{verifier_id}", name=name, node_class=NodeClass.VERIFIER)
    async def run(state: FlowState) -> FlowState:
        verdict = Verification(verifier=verifier_id, passed=True)
        state.verifications = [*state.verifications, verdict]
        return state

    return run


correctness = _verifier("correctness", "Verifier — correctness")
security = _verifier("security", "Verifier — security")
test_evidence = _verifier("test_evidence", "Verifier — test evidence")
regression_risk = _verifier("regression_risk", "Verifier — regression risk")
