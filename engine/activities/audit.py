"""Writing the run tree and the audit trail.

Every activity here is idempotent. On replay an activity may run again, and an audit trail
that double-records is not evidence — so each external mutation is keyed on `run_id` plus
the step, and a repeat is a no-op rather than a second row.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from temporalio import activity

from engine.db import connect


@dataclass
class RunStarted:
    run_id: UUID
    root_run_id: UUID
    parent_run_id: UUID | None
    app_id: UUID
    workflow_id: str
    ticket_system: str
    ticket_id: str
    risk_tier: str
    schema_version: int
    policy_commit: str
    policy_bundle: dict[str, Any] = field(default_factory=dict)


@dataclass
class EventRecorded:
    run_id: UUID
    seq: int
    kind: str
    node_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    # Never defaulted. `None` means "this event represents no external effect", which the
    # schema enforces; it does not mean "we did not check" — invariant 11.
    control_mode: str | None = None


@dataclass
class RunEnded:
    run_id: UUID
    status: str


@activity.defn
async def record_run_started(run: RunStarted) -> None:
    async with connect() as conn:
        await conn.execute(
            """
            INSERT INTO flow_runs (id, parent_run_id, root_run_id, app_id, workflow_id,
                                   ticket_system, ticket_id, risk_tier, status,
                                   schema_version, policy_commit, policy_bundle)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'running',$9,$10,$11)
            ON CONFLICT (id) DO NOTHING
            """,
            run.run_id,
            run.parent_run_id,
            run.root_run_id,
            run.app_id,
            run.workflow_id,
            run.ticket_system,
            run.ticket_id,
            run.risk_tier,
            run.schema_version,
            run.policy_commit,
            json.dumps(run.policy_bundle),
        )


@activity.defn
async def record_event(event: EventRecorded) -> None:
    async with connect() as conn:
        await conn.execute(
            """
            INSERT INTO flow_events (run_id, seq, kind, node_id, payload, control_mode)
            VALUES ($1,$2,$3,$4,$5,$6)
            ON CONFLICT (run_id, seq) DO NOTHING
            """,
            event.run_id,
            event.seq,
            event.kind,
            event.node_id,
            json.dumps(event.payload),
            event.control_mode,
        )


@activity.defn
async def record_run_ended(ended: RunEnded) -> None:
    async with connect() as conn:
        await conn.execute(
            "UPDATE flow_runs SET status = $2, ended_at = now() WHERE id = $1",
            ended.run_id,
            ended.status,
        )
