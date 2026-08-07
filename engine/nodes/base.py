"""The node contract, and the mechanism that makes node classes enforceable.

Every node has the same signature — `(FlowState) -> FlowState` — so any node can later be
replaced by a child flow without changing its callers (ADR 0002, "Recursive composition").

The class of a node is not documentation. `current_node()` exposes which node is executing,
and `engine.adapters.llm` refuses to serve a `deterministic` one. Invariant 1 says the Flow
Engine contains no LLM; this is where that stops being a promise.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from engine.state import FlowState, NodeClass

type NodeFn = Callable[[FlowState], Awaitable[FlowState]]


@dataclass(frozen=True)
class NodeSpec:
    id: str
    name: str
    node_class: NodeClass

    @property
    def may_call_llm(self) -> bool:
        return self.node_class is not NodeClass.DETERMINISTIC


REGISTRY: dict[str, NodeSpec] = {}

_current: ContextVar[NodeSpec | None] = ContextVar("current_node", default=None)


def current_node() -> NodeSpec | None:
    return _current.get()


@contextmanager
def executing(spec: NodeSpec):  # type: ignore[no-untyped-def]
    token = _current.set(spec)
    try:
        yield
    finally:
        _current.reset(token)


def node(node_id: str, name: str, node_class: NodeClass) -> Callable[[NodeFn], NodeFn]:
    """Register a node and bind its class for the duration of every execution."""

    def decorate(fn: NodeFn) -> NodeFn:
        spec = NodeSpec(id=node_id, name=name, node_class=node_class)
        if node_id in REGISTRY:
            raise ValueError(f"duplicate node id {node_id!r}")
        REGISTRY[node_id] = spec

        async def wrapper(state: FlowState) -> FlowState:
            with executing(spec):
                return await fn(state)

        wrapper.__name__ = fn.__name__
        wrapper.__doc__ = fn.__doc__
        wrapper.spec = spec  # type: ignore[attr-defined]
        return wrapper

    return decorate
