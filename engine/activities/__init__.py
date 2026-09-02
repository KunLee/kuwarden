"""Everything with a side effect. Retried, and therefore idempotent.

On replay an activity may run again. Opening a PR twice, commenting on a ticket three times,
or deploying twice are all real failures this rule prevents — every external mutation is
keyed on `run_id` plus the step.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from engine.activities.audit import (
    record_event,
    record_run_ended,
    record_run_files,
    record_run_started,
    record_run_status,
)
from engine.activities.nodes import read_preview_url, run_node
from engine.activities.notify import notify_gate_reached

#: The single registration list. The worker and the tests both read it, because two lists
#: drift and the symptom is a workflow failing on an activity nobody noticed was missing.
ALL: Sequence[Callable[..., Any]] = [
    run_node,
    read_preview_url,
    record_run_started,
    record_run_status,
    record_event,
    record_run_ended,
    record_run_files,
    notify_gate_reached,
]

__all__ = [
    "ALL",
    "notify_gate_reached",
    "read_preview_url",
    "record_event",
    "record_run_ended",
    "record_run_files",
    "record_run_started",
    "record_run_status",
    "run_node",
]
