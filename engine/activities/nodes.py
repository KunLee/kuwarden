"""Node execution as an activity.

Nodes have side effects and hold the only LLM calls in the system, so they are activities,
never workflow code. This module is the seam: `flows/` imports from here and never from
`nodes/`, which keeps the determinism boundary visible in the import graph rather than
resting on everyone remembering it.

It is also where a node's `NodeContext` is bound — configuration and a credential broker,
neither of which may travel on `FlowState`.
"""

from __future__ import annotations

from dataclasses import dataclass

from temporalio import activity

from engine.adapters.credentials import CredentialBroker, EnvCredentialBroker
from engine.config import AppConfig
from engine.nodes import NODES
from engine.nodes.base import NodeContext, bound
from engine.state import FlowState


@dataclass
class NodeInput:
    node_id: str
    state: FlowState


class NodeRuntime:
    """What the worker knows and the workflow must not.

    Configuration and credentials are worker-side facts. Putting them in `FlowInput` would
    serialise them into workflow history, which is the audit record — the one place a token
    must never reach.
    """

    def __init__(self) -> None:
        self._config: AppConfig | None = None
        self._broker: CredentialBroker = EnvCredentialBroker()
        self._transport: object | None = None

    def configure(
        self,
        config: AppConfig,
        broker: CredentialBroker | None = None,
        transport: object | None = None,
    ) -> None:
        self._config = config
        if broker is not None:
            self._broker = broker
        self._transport = transport

    def context(self) -> NodeContext:
        if self._config is None:
            raise RuntimeError("worker has no kuwarden.yaml loaded; call configure() first")
        return NodeContext(
            config=self._config,
            broker=self._broker,
            transport=self._transport,  # type: ignore[arg-type]
        )


RUNTIME = NodeRuntime()


@activity.defn
async def run_node(params: NodeInput) -> FlowState:
    fn = NODES.get(params.node_id)
    if fn is None:
        raise LookupError(f"unknown node {params.node_id!r}")
    with bound(RUNTIME.context()):
        return await fn(params.state)
