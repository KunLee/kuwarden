"""The delivery flow — DETERMINISTIC.

No wall clock, no randomness, no I/O, no network, no LLM. This code is replayed on recovery
and must produce identical decisions, so every side effect is an activity and nothing is
imported from `nodes/` or `adapters/`.

The shape is ADR 0002: a router at intake, a bounded `Coder ⇄ Build & Test` cycle, verifiers
fanning out in fresh context, a gate whose depth comes from `risk_tier`, then release. ADR
0007 splits the push out of Release and puts it inside the cycle, so the branch exists before
anything grades it.
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
    from engine.activities.notify import GateNotice, notify_gate_reached
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


def _failure(exc: BaseException) -> dict[str, Any]:
    """Reduce an exception to the two things a reader needs: what kind, and what it said.

    Temporal wraps an activity failure in `ActivityError` whose cause carries the original
    type name and message, so the innermost cause is walked to. Both are reconstructed from
    workflow history on replay, which makes this deterministic — a `repr()` of the live
    exception object would not be.

    The message is truncated. It reaches an append-only table, and a stack trace pasted into
    an audit row is unreadable there and cannot be removed later.
    """
    cause: BaseException = exc
    while cause.__cause__ is not None and cause.__cause__ is not cause:
        cause = cause.__cause__
    kind = getattr(cause, "type", None) or type(cause).__name__
    return {"error": str(kind), "message": str(cause)[:500]}


class _Rejected(Exception):
    """The change did not survive. Routed to compensation, not to a crash."""


@workflow.defn(name="DeliveryFlow")
class DeliveryFlow:
    def __init__(self) -> None:
        self._run_id: UUID | None = None
        #: Selects which application's credentials resolve. Set once at run start.
        self._app_id: UUID | None = None
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
        self._app_id = params.app_id
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
            await self._emit_cleanup(state)
            self._status = "rejected"
        except Exception as exc:
            # Compensation is driven from outside the thing that failed, because a crashed
            # process cannot clean up after itself.
            #
            # Emitted before compensating, so the record reads in the order things happened:
            # what broke, then what was done about it.
            await self._emit("run_failed", payload=_failure(exc))
            state = await self._node_step("compensate", state)
            await self._emit_cleanup(state)
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

            # Pushed before it is graded, because CI cannot run on a branch that does not
            # exist — ADR 0007. No control point moves: `protected_paths` is denied before the
            # push rather than after it, the branch is namespaced, and no pull request is
            # opened until the verifiers and the gate have both been satisfied.
            state = await self._node_step("push", state)
            # No `control_mode`. It belongs to `external_effect` events, which name one of the
            # three control points in ADR 0004; a branch push is not one of them, and calling
            # it `authorized` would widen a word whose whole value is that it is narrow.
            await self._emit(
                "branch_pushed",
                node_id="push",
                payload={
                    "branch": state.branch,
                    "commit": state.head_commit,
                    "base": state.base_commit,
                    "attempt": attempt,
                },
            )

            state = await self._node_step("build_test", state)
            if state.ci_result is not None:
                # `source` travels with the verdict into the audit trail, so the evidence an
                # approver reads says who executed the tests. Recording only the exit code
                # would let a sandbox result be read later as a CI result -- invariant 3.
                await self._emit(
                    "build_test_verdict",
                    node_id="build_test",
                    payload={
                        "exit_code": state.ci_result.exit_code,
                        "source": state.ci_result.source,
                        "duration_ms": state.ci_result.duration_ms,
                        "url": state.ci_result.url,
                        "independent_anchor": state.ci_result.is_external_anchor,
                        # Both graders travel, and so does how the verdict was reached. A run
                        # whose CI was never consulted must not read, later, like one whose CI
                        # passed.
                        "sandbox_exit_code": (
                            state.sandbox_result.exit_code if state.sandbox_result else None
                        ),
                        "ci_detail": state.ci_detail,
                    },
                )
            if state.ci_result is not None and state.ci_result.exit_code == 0:
                break
        else:
            raise _Rejected("retry budget exhausted")

        # Recorded once the inner loop has finished, so the run's own record says under what
        # isolation its code was executed. A degradation that lives only in a log line and a
        # UI banner does not appear in any report anyone exports.
        if state.sandbox_isolation is not None:
            await self._emit(
                "sandbox_isolation",
                payload={"state": state.sandbox_isolation, "gaps": state.sandbox_gaps},
            )

        # Second and authoritative tiering stage — the first point at which a diff exists.
        state.risk_tier = self._final_tier(state)
        await self._emit("risk_tier_final", payload={"tier": state.risk_tier})

        state = await self._verify(state)
        if not all(v.passed for v in state.verifications):
            raise _Rejected("a verifier falsified the change")

        state = await self._gate(state, params)
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

    def _run_id_checked(self) -> UUID:
        """The run id, once the workflow has started. Narrows the Optional for callers."""
        assert self._run_id is not None
        return self._run_id

    async def _gate(self, state: FlowState, params: FlowInput) -> FlowState:
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

        # Sent once, before suspending. Nobody is watching a dashboard for a run that may sit
        # here for a day; without this the gate is a queue with no doorbell. The email is only
        # a notification -- the decision is made in the Workbench against the evidence
        # document, never by replying to a message (ADR 0003 §6).
        await workflow.execute_activity(
            notify_gate_reached,
            GateNotice(
                run_id=self._run_id_checked(),
                app_id=params.app_id,
                ticket_id=state.ticket.id,
                risk_tier=state.risk_tier,
                approvals_needed=needed,
            ),
            start_to_close_timeout=AUDIT_TIMEOUT,
            retry_policy=RetryPolicy(maximum_attempts=3),
        )

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
        try:
            result: FlowState = await self._execute(node_id, state)
        except Exception as exc:
            # Recorded, then re-raised. Without this the trail shows `node_started` and then
            # nothing at all, and a reader has to infer the failure from a missing row and
            # can never learn the reason. For a product whose claim is the audit trail, "the
            # run failed and the record does not say why" is the worst possible gap.
            #
            # `record=False` is the verifier fan-out, whose bracketing events are emitted by
            # `_verify`; a failure there is still worth a row, so this ignores the flag.
            await self._emit("node_failed", node_id=node_id, payload=_failure(exc))
            raise
        if record:
            await self._emit("node_completed", node_id=node_id)
        return result

    async def _execute(self, node_id: str, state: FlowState) -> FlowState:
        return await workflow.execute_activity(
            run_node,
            NodeInput(node_id=node_id, state=state, app_id=self._app_id),
            # Named rather than left as Temporal's counter. The workflow history is the only
            # place a stack trace survives, and correlating "activity 6" to a node otherwise
            # means decoding the activity's input — which carries the whole FlowState. The
            # sequence keeps it unique across the Coder loop's repeats, and is assigned in
            # workflow code so a replay produces the same ids.
            activity_id=f"{node_id}#{self._seq}",
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
                    # A rejected API key is still rejected on the third attempt. Retrying it
                    # costs a minute of the run's wall clock and puts three refused requests
                    # on somebody's account instead of one. Rate limits and 5xx stay
                    # retryable — they are a different class and keep the base `LLMError`.
                    "LLMAuthError",
                    # Same reasoning: the same prompt under the same cap truncates at
                    # the same place, and each attempt is minutes and a full charge.
                    "LLMOutputTruncated",
                ],
            ),
        )

    async def _emit_cleanup(self, state: FlowState) -> None:
        """Record what compensation did.

        Cleanup that leaves no trace is indistinguishable from cleanup that never ran, and
        "the branch is gone" is a fact somebody will later want attributed to a decision
        rather than to a person with a delete button.
        """
        if state.cleanup:
            await self._emit("compensated", node_id="compensate", payload={"detail": state.cleanup})

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
