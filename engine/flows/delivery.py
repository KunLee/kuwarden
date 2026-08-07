"""The delivery flow — DETERMINISTIC.

No wall clock, no randomness, no I/O, no network, no LLM. This code is replayed on recovery
and must produce identical decisions, so every side effect is an activity and nothing is
imported from `nodes/` or `adapters/`.

The shape is ADR 0002: a router at intake, a bounded `Coder ⇄ Build & Test` cycle, verifiers
fanning out in fresh context, a gate whose depth comes from `risk_tier`, then release.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from uuid import UUID

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from engine.activities.audit import (
        EventRecorded,
        RunEnded,
        RunStarted,
        record_event,
        record_run_ended,
        record_run_started,
    )
    from engine.activities.nodes import NodeInput, run_node
    from engine.policy.tiering import assert_not_lowered, raise_to, required_approvals
    from engine.state import SCHEMA_VERSION, Approval, FlowState, RiskTier, Ticket

VERIFIERS = (
    "verifier.correctness",
    "verifier.security",
    "verifier.test_evidence",
    "verifier.regression_risk",
)

NODE_TIMEOUT = timedelta(minutes=30)
AUDIT_TIMEOUT = timedelta(seconds=30)


@dataclass
class FlowInput:
    run_id: UUID
    app_id: UUID
    ticket: Ticket
    policy_commit: str
    policy_bundle: dict[str, Any] = field(default_factory=dict)
    provisional_risk_tier: RiskTier = "low"
    budget_cents_allowed: int = 0
    max_coder_retries: int = 3
    parent_run_id: UUID | None = None
    root_run_id: UUID | None = None


@dataclass
class FlowResult:
    run_id: UUID
    status: str
    risk_tier: RiskTier
    events: int


@dataclass
class ApprovalSignal:
    principal: str
    approved: bool
    evidence_digest: str
    comment: str = ""


class _Rejected(Exception):
    """The change did not survive. Routed to compensation, not to a crash."""


@workflow.defn(name="DeliveryFlow")
class DeliveryFlow:
    def __init__(self) -> None:
        self._run_id: UUID | None = None
        self._seq = 0
        self._approvals: list[ApprovalSignal] = []
        self._status = "running"
        self._node: str | None = None

    # --- signals and queries -------------------------------------------------------------

    @workflow.signal
    async def approve(self, signal: ApprovalSignal) -> None:
        """A human decides. Nothing else may satisfy an approval requirement."""
        self._approvals.append(signal)

    @workflow.query
    def status(self) -> dict[str, Any]:
        return {"status": self._status, "node": self._node, "approvals": len(self._approvals)}

    # --- the run -------------------------------------------------------------------------

    @workflow.run
    async def run(self, params: FlowInput) -> FlowResult:
        self._run_id = params.run_id
        root_run_id = params.root_run_id or params.run_id

        state = FlowState(
            run_id=params.run_id,
            root_run_id=root_run_id,
            parent_run_id=params.parent_run_id,
            ticket=params.ticket,
            policy_commit=params.policy_commit,
            policy_bundle=params.policy_bundle,
            risk_tier=params.provisional_risk_tier,
            budget_cents_allowed=params.budget_cents_allowed,
        )

        await workflow.execute_activity(
            record_run_started,
            RunStarted(
                run_id=params.run_id,
                root_run_id=root_run_id,
                parent_run_id=params.parent_run_id,
                app_id=params.app_id,
                workflow_id=workflow.info().workflow_id,
                ticket_system=params.ticket.system,
                ticket_id=params.ticket.id,
                risk_tier=state.risk_tier,
                schema_version=SCHEMA_VERSION,
                policy_commit=params.policy_commit,
                policy_bundle=params.policy_bundle,
            ),
            start_to_close_timeout=AUDIT_TIMEOUT,
        )

        try:
            state = await self._deliver(state, params)
            self._status = "succeeded"
        except _Rejected as rejected:
            await self._emit("aborting", payload={"reason": str(rejected)})
            state = await self._node_step("compensate", state)
            self._status = "rejected"
        except Exception:
            # Compensation is driven from outside the thing that failed, because a crashed
            # process cannot clean up after itself.
            state = await self._node_step("compensate", state)
            self._status = "failed"
            await self._node_step("reporter", state)
            await self._finish()
            raise
        else:
            state = await self._node_step("reporter", state)
            await self._finish()
            return FlowResult(
                run_id=params.run_id,
                status=self._status,
                risk_tier=state.risk_tier,
                events=self._seq,
            )

        state = await self._node_step("reporter", state)
        await self._finish()
        return FlowResult(
            run_id=params.run_id,
            status=self._status,
            risk_tier=state.risk_tier,
            events=self._seq,
        )

    async def _deliver(self, state: FlowState, params: FlowInput) -> FlowState:
        state = await self._node_step("triage", state)
        state = await self._node_step("planner", state)

        # ③ ⇄ ④. The feedback edge is the mechanism; retrying without it is repeated
        # guessing. Bounded, because an unbounded loop is an unbounded bill.
        for attempt in range(params.max_coder_retries + 1):
            state.retry_count = attempt
            state = await self._node_step("coder", state)
            state = await self._node_step("build_test", state)
            if state.ci_result is not None and state.ci_result.exit_code == 0:
                break
        else:
            raise _Rejected("retry budget exhausted")

        # Second and authoritative tiering stage — the first point at which a diff exists.
        state.risk_tier = self._final_tier(state)
        await self._emit("risk_tier_final", payload={"tier": state.risk_tier})

        state = await self._verify(state)
        if not all(v.passed for v in state.verifications):
            raise _Rejected("a verifier falsified the change")

        state = await self._gate(state)
        return await self._node_step("release", state)

    def _final_tier(self, state: FlowState) -> RiskTier:
        """Rules-first, over the actual diff.

        The rules are empty while the nodes are stubs. What is being fixed now is that the
        result may only ever move upward, at both stages, by anything.
        """
        proposed: RiskTier = state.risk_tier
        assert_not_lowered(state.risk_tier, proposed)
        return raise_to(state.risk_tier, proposed)

    async def _verify(self, state: FlowState) -> FlowState:
        """⑤ Fan-out in fresh context.

        Each verifier is a separate activity precisely so that none of them shares a context
        with the Coder or with each other. Events bracket the whole fan-out rather than each
        branch: per-branch events would be sequenced by activity completion order, which is
        replay-stable but needlessly hard to reason about.
        """
        await self._emit("verifiers_started", payload={"count": len(VERIFIERS)})
        results = await asyncio.gather(
            *(self._node_step(v, state, record=False) for v in VERIFIERS)
        )
        for result in results:
            state.verifications = [*state.verifications, *result.verifications]
        await self._emit(
            "verifiers_completed",
            payload={"passed": sum(1 for v in state.verifications if v.passed)},
        )
        return state

    async def _gate(self, state: FlowState) -> FlowState:
        """⑥ Approval gate. Suspends without holding a resource open.

        `low` requires nobody. That is the point of tiering: uniform gating turns the
        platform into a queue in front of a human, which is the constraint it exists to
        relieve.
        """
        needed = required_approvals(state.risk_tier)
        await self._emit("gate_reached", payload={"tier": state.risk_tier, "needed": needed})

        if needed == 0:
            await self._emit("gate_passed", payload={"auto": True})
            return state

        self._status = "suspended"
        await workflow.wait_condition(
            lambda: any(not a.approved for a in self._approvals)
            or sum(1 for a in self._approvals if a.approved) >= needed
        )
        self._status = "running"

        # What the approver was shown, not merely that they clicked approve.
        state.approvals = [
            Approval(
                principal=a.principal,
                approved=a.approved,
                risk_tier=state.risk_tier,
                evidence_digest=a.evidence_digest,
                comment=a.comment,
            )
            for a in self._approvals
        ]

        if any(not a.approved for a in self._approvals):
            await self._emit("gate_rejected")
            raise _Rejected("rejected by an approver")

        await self._emit("gate_passed", payload={"auto": False})
        return state

    # --- plumbing ------------------------------------------------------------------------

    async def _node_step(self, node_id: str, state: FlowState, record: bool = True) -> FlowState:
        self._node = node_id
        if record:
            await self._emit("node_started", node_id=node_id)
        result: FlowState = await workflow.execute_activity(
            run_node,
            NodeInput(node_id=node_id, state=state),
            start_to_close_timeout=NODE_TIMEOUT,
            retry_policy=RetryPolicy(
                maximum_attempts=3,
                # Retrying a refusal is not resilience. A ticket outside the declared scope
                # will still be outside it three attempts later, and each attempt is another
                # round of calls to someone else's platform.
                non_retryable_error_types=[
                    "PolicyDenied",
                    "ProtectedPathWritten",
                    "RiskTierLowered",
                    "InvariantViolation",
                    "ConfigError",
                ],
            ),
        )
        if record:
            await self._emit("node_completed", node_id=node_id)
        return result

    async def _emit(
        self,
        kind: str,
        node_id: str | None = None,
        payload: dict[str, Any] | None = None,
        control_mode: str | None = None,
    ) -> None:
        assert self._run_id is not None
        self._seq += 1
        await workflow.execute_activity(
            record_event,
            EventRecorded(
                run_id=self._run_id,
                seq=self._seq,
                kind=kind,
                node_id=node_id,
                payload=payload or {},
                control_mode=control_mode,
            ),
            start_to_close_timeout=AUDIT_TIMEOUT,
        )

    async def _finish(self) -> None:
        assert self._run_id is not None
        await workflow.execute_activity(
            record_run_ended,
            RunEnded(run_id=self._run_id, status=self._status),
            start_to_close_timeout=AUDIT_TIMEOUT,
        )
