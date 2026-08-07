"""Everything with a side effect. Retried, and therefore idempotent.

On replay an activity may run again. Opening a PR twice, commenting on a ticket three times,
or deploying twice are all real failures this rule prevents — every external mutation is
keyed on `run_id` plus the step.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from engine.activities.audit import record_event, record_run_ended, record_run_started
from engine.activities.nodes import run_node

ALL: Sequence[Callable[..., Any]] = [
    run_node,
    record_run_started,
    record_event,
    record_run_ended,
]

__all__ = ["ALL", "record_event", "record_run_ended", "record_run_started", "run_node"]
