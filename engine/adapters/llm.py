"""The only door to a model.

Not yet an adapter — the walking skeleton runs with every node empty, deliberately, so that
the control plane is proven before a model is introduced. What exists now is the guard,
because the guard is the part that is expensive to add later: once several nodes call models
through several paths, "no LLM in deterministic code" becomes an audit instead of a check.

Model identifiers do not belong here or in any strategy document. They live in
`docs/reference/models.md` with a review date.
"""

from __future__ import annotations

from engine.errors import InvariantViolation
from engine.nodes.base import current_node


def assert_may_call_llm() -> None:
    """Refuse to serve a model call from anywhere that must not make one.

    Two failures are caught. A `deterministic` node calling a model violates its declared
    class. Code with no node context at all is workflow or activity plumbing — the Flow
    Engine — and invariant 1 says the Flow Engine contains no LLM.
    """
    spec = current_node()
    if spec is None:
        raise InvariantViolation(
            "LLM call attempted outside any node. The Flow Engine contains no LLM (invariant 1)."
        )
    if not spec.may_call_llm:
        raise InvariantViolation(
            f"node {spec.id!r} is classified {spec.node_class.value!r} and may not call a model"
        )


async def complete(prompt: str) -> str:
    """Placeholder. Raises unless the caller is entitled to a model at all."""
    assert_may_call_llm()
    raise NotImplementedError(
        "No LLM adapter yet, by design. Nodes are proven empty first — see log/."
    )
