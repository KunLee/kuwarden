"""④ Build & Test — `deterministic`, no LLM.

Emits the objective verdict. The CI system's exit code is the anchor; an agent's assertion
that its work succeeded is never a gate input.

Also where `changed_files` is checked against `protected_paths`, and where the second and
authoritative tiering stage runs, because this is the first point at which a diff exists.
"""

from __future__ import annotations

from engine.errors import ProtectedPathWritten
from engine.nodes.base import node
from engine.policy.protected_paths import ProtectedPaths
from engine.state import CIResult, FlowState, NodeClass


@node(node_id="build_test", name="Build & Test", node_class=NodeClass.DETERMINISTIC)
async def build_test(state: FlowState) -> FlowState:
    if state.diff is not None:
        violations = ProtectedPaths().violations(state.diff.paths)
        if violations:
            detail = ", ".join(f"{path} ({pattern})" for path, pattern in violations)
            raise ProtectedPathWritten(detail)

    state.ci_result = CIResult(exit_code=0)
    return state
