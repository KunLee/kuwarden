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
class ChangedFile:
    path: str
    added: int
    removed: int


@dataclass
class RunFiles:
    """Which files one push put on the branch, from git's own numstat.

    Not the model's account of what it wrote. The `Diff` this is built from is read from git
    after the Coder's loop, and an agent's claim about its own change is never an input —
    invariant 3, applied to the index.
    """

    run_id: UUID
    attempt: int
    files: list[ChangedFile] = field(default_factory=list)


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


@dataclass
class RunStatusChanged:
    run_id: UUID
    status: str


@activity.defn
async def record_run_status(change: RunStatusChanged) -> None:
    """Persist that a run is waiting for a human, or has stopped waiting.

    `flow_runs.status` used to be written twice — `running` at the start and the final status
    at the end — while suspension lived only in the workflow object's memory. Everything that
    asks "is this run waiting for me" reads the column: the approval endpoint refuses a
    decision unless it says `suspended`, and the Workbench hides the approval panel. So a gate
    that suspended without recording it was a gate no human could pass.

    Idempotent, and guarded on the current status: replay may run this again, and a run that
    has since ended must not be dragged back to `suspended` by a late retry.
    """
    async with connect() as conn:
        await conn.execute(
            "UPDATE flow_runs SET status = $2 "
            "WHERE id = $1 AND status IN ('running', 'suspended')",
            change.run_id,
            change.status,
        )


@activity.defn
async def record_run_files(run: RunFiles) -> None:
    """Index this run's changed files — ADR 0012.

    Idempotent by upsert rather than by insert-and-ignore, because a run pushes more than once
    and the later attempt is the one on the branch. `ON CONFLICT DO NOTHING` would freeze the
    first attempt's line counts and quietly describe a change that was superseded.

    An empty diff writes nothing rather than clearing the run's rows: the flow only reaches
    here after a push, and nothing to record at that point means the push was deduplicated,
    not that the earlier files were reverted.
    """
    if not run.files:
        return
    async with connect() as conn:
        await conn.executemany(
            """
            INSERT INTO run_files (run_id, path, added, removed, attempt)
            VALUES ($1,$2,$3,$4,$5)
            ON CONFLICT (run_id, path) DO UPDATE
                SET added = EXCLUDED.added,
                    removed = EXCLUDED.removed,
                    attempt = EXCLUDED.attempt
            """,
            [(run.run_id, f.path, f.added, f.removed, run.attempt) for f in run.files],
        )


@activity.defn
async def record_run_ended(ended: RunEnded) -> None:
    async with connect() as conn:
        await conn.execute(
            "UPDATE flow_runs SET status = $2, ended_at = now() WHERE id = $1",
            ended.run_id,
            ended.status,
        )
