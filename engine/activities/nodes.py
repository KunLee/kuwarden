"""Node execution as an activity.

Nodes have side effects and hold the only LLM calls in the system, so they are activities,
never workflow code. This module is the seam: `flows/` imports from here and never from
`nodes/`, which keeps the determinism boundary visible in the import graph rather than
resting on everyone remembering it.

It is also where a node's `NodeContext` is bound — configuration and a credential broker,
neither of which may travel on `FlowState`.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from uuid import UUID

from temporalio import activity

from engine import config_store
from engine.adapters.credentials import (
    CredentialBroker,
    CredentialRequest,
    EnvCredentialBroker,
    Secret,
)
from engine.adapters.secrets import EncryptedPostgresStore
from engine.config import AppConfig
from engine.errors import PolicyDenied
from engine.nodes import NODES
from engine.nodes.base import NodeContext, bound
from engine.sandbox import Sandbox
from engine.sandbox.podman import PodmanSandbox
from engine.state import FlowState


@dataclass
class NodeInput:
    """What a node needs that is not in `FlowState`.

    `app_id` is here rather than on `FlowState` because it selects *which application's
    credentials* resolve, and that is a worker-side concern. Putting it on the state would
    also serialise it into workflow history, which is fine for an id and sets the wrong
    precedent for the broker it selects.
    """

    node_id: str
    state: FlowState
    app_id: UUID | None = None


class NodeRuntime:
    """What the worker knows and the workflow must not.

    Configuration and credentials are worker-side facts. Putting them in `FlowInput` would
    serialise them into workflow history, which is the audit record — the one place a token
    must never reach.
    """

    def __init__(self) -> None:
        self._config: AppConfig | None = None
        self._broker: CredentialBroker = EnvCredentialBroker()
        #: Set only when a caller passed one. Distinguishes "a test injected this" from
        #: "nobody said, so it is the environment default".
        self._explicit_broker: CredentialBroker | None = None
        self._transport: object | None = None
        self._sandbox: Sandbox | None = None

    def configure(
        self,
        config: AppConfig | None = None,
        broker: CredentialBroker | None = None,
        transport: object | None = None,
        sandbox: Sandbox | None = None,
    ) -> None:
        """Bind worker-wide facts.

        `config` is optional. A worker serving many applications resolves configuration per
        run and needs none of its own; passing one makes it the fallback for applications
        that have no stored configuration yet.
        """
        self._config = config
        if broker is not None:
            self._broker = broker
            self._explicit_broker = broker
        self._transport = transport
        # Kept only when a caller injected one — a test's stub. Otherwise the sandbox is built
        # per context from the configuration that governs *that* run: `require_full_isolation`
        # is a per-application declaration, and binding it once at startup meant an
        # application asking for strict isolation silently ran under another's relaxed
        # setting. A sandbox reporting a bound it is not applying is the failure ADR 0005 is
        # written against.
        self._sandbox = sandbox

    @property
    def configured_config(self) -> AppConfig | None:
        """What this worker was started with, or what a test fixture injected.

        The fallback for an application with no stored configuration. Exposed rather than read
        from the file inside `config_store`, so a test that injects a config is not quietly
        overridden by whatever `kuwarden.yaml` is in the working directory.
        """
        return self._config

    def context(self, app_id: UUID | None = None, config: AppConfig | None = None) -> NodeContext:
        """Bind a node's context, resolving credentials for one application.

        When `app_id` is given the broker reads the encrypted store first and falls back to
        the environment. That order matters: the Workbench writes at runtime and the
        environment is fixed at process start, so a credential someone just entered must win
        over a stale variable that was exported months ago.

        `config` is passed in rather than resolved here because resolving it reads the
        database, and this is a synchronous call on the hot path of every node. `run_node`
        awaits `config_store.resolve` and hands the answer down. Omitted, the worker's own
        startup configuration is used — which is what every test does, and what a
        single-application deployment did before per-application configuration existed.
        """
        effective = config or self._config
        if effective is None:
            raise RuntimeError("worker has no kuwarden.yaml loaded; call configure() first")
        return NodeContext(
            config=effective,
            broker=self._broker_for(app_id),
            transport=self._transport,  # type: ignore[arg-type]
            # Constructing this per context is cheap: the expensive part is probing the
            # host's capabilities, and that result is cached for the process in
            # `engine.sandbox.podman`.
            sandbox=self._sandbox
            or PodmanSandbox(
                require_full_isolation=effective.sandbox.require_full_isolation
            ),
        )

    def _broker_for(self, app_id: UUID | None) -> CredentialBroker:
        """The credential broker for one application.

        `_explicit_broker` is what a test injected. Honoured unconditionally, because a test
        that silently reached a real encrypted store would be reading whatever happens to be
        in the developer's database.
        """
        if self._explicit_broker is not None or app_id is None:
            return self._broker
        return StoreThenEnvBroker(app_id)


class StoreThenEnvBroker:
    """Resolve from the encrypted store, then from the environment.

    The store is authoritative because it is the only one that can be written while the
    process is running — that write path is the entire reason ADR 0006 exists. The
    environment stays as a fallback so an air-gapped operator can inject a credential without
    a working Workbench, and so the existing development setup keeps working unchanged.

    A `PolicyDenied` from the store means "nothing stored for this slot", which is a normal
    state during setup and must fall through. A `SecretKeyError` means a credential *is*
    stored and the master key cannot open it — that is a misconfiguration, not an absence, and
    silently falling back to an environment variable would hide it.
    """

    def __init__(self, app_id: UUID) -> None:
        self._store = EncryptedPostgresStore(app_id)
        self._env = EnvCredentialBroker()
        self._app_id = app_id

    async def resolve(self, request: CredentialRequest) -> Secret:
        try:
            return await self._store.resolve(request)
        except PolicyDenied:
            pass
        try:
            return await self._env.resolve(request)
        except PolicyDenied:
            raise PolicyDenied(
                f"no credential for {request.kind.value} (realm {request.realm!r}): none "
                f"stored for application {self._app_id} in the Workbench, and none in the "
                f"environment"
            ) from None


log = logging.getLogger(__name__)

RUNTIME = NodeRuntime()


@activity.defn
async def run_node(params: NodeInput) -> FlowState:
    """Execute one node, and say so.

    The logging lives here rather than in each node because this is the single choke point
    every node passes through: one place to change, and no node can be added that forgets to
    announce itself.

    **Every line carries the run id.** A worker serves many runs concurrently and their
    activities interleave, so a log line without one is a line nobody can attribute — which
    is the same as no line at all when two runs are in flight.

    This is deliberately not the audit trail. `flow_events` is the record (invariant 9,
    append-only, enforced by a trigger); these lines are operational and disposable. Anything
    that matters for evidence goes to the trail, never only to a log.
    """
    fn = NODES.get(params.node_id)
    if fn is None:
        raise LookupError(f"unknown node {params.node_id!r}")

    run_id = params.state.run_id
    log.info("run %s | %s | started", run_id, params.node_id)
    started = time.monotonic()
    try:
        # Resolved per run, not per worker. Which application's configuration governs this
        # node is the question a single-tenant worker could not answer — see
        # engine/config_store.py.
        config = await config_store.resolve(
            params.app_id, fallback=RUNTIME.configured_config
        )
        with bound(RUNTIME.context(params.app_id, config=config)):
            result = await fn(params.state)
    except Exception as exc:
        # Logged and re-raised, never swallowed. Temporal decides whether this is retryable;
        # the log exists so a human reading the window sees which node broke and why without
        # opening the workflow history.
        log.warning(
            "run %s | %s | FAILED after %dms: %s: %s",
            run_id,
            params.node_id,
            int((time.monotonic() - started) * 1000),
            type(exc).__name__,
            exc,
        )
        raise
    log.info(
        "run %s | %s | ok in %dms",
        run_id,
        params.node_id,
        int((time.monotonic() - started) * 1000),
    )
    return result
