"""What an approver is shown, and the digest that binds them to it.

ADR 0003 §6 asks for more than "someone clicked approve". The record has to say *what they
were looking at*, because an approval is only meaningful against specific evidence — and the
run keeps moving, so evidence assembled at 10:04 is not necessarily evidence assembled at
10:06.

The mechanism is a digest:

1. The Workbench renders the evidence document and receives its digest.
2. The approver decides, and the decision is submitted **with that digest**.
3. The API recomputes the digest and refuses the approval if it has changed.

That turns "approved run X" into "approved this exact set of facts about run X", which is the
difference between an audit trail and a log of button presses.

Assembly lives here rather than in the API module so that the digest is computed by one
function. Two implementations of "canonical form" that drift apart would silently invalidate
every comparison, and the failure would look like approvals randomly bouncing.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from engine.db import connect
from engine.errors import KuWardenError

#: Bumped when the shape of the assembled document changes. It is part of the digest input,
#: so a format change cannot make an old digest accidentally match a new document.
EVIDENCE_SCHEMA = 1


class RunNotFound(KuWardenError):
    """No such run."""


@dataclass(frozen=True)
class Evidence:
    """A run's decision-relevant facts, and the digest of exactly this content."""

    run_id: UUID
    digest: str
    document: dict[str, Any]


def digest_of(document: dict[str, Any]) -> str:
    """Hash a document in a form that does not depend on dictionary ordering.

    `sort_keys` and a fixed separator are the whole point: without them the digest changes
    when an unrelated refactor reorders a dict literal, and every in-flight approval breaks
    for no reason a reader could diagnose.
    """
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


async def assemble(run_id: UUID) -> Evidence:
    """Build the evidence document for a run from the audit trail.

    Read from `flow_events` rather than from the workflow's in-memory state: the audit trail
    is the record (invariant 9), and an approver must be shown the same thing a regulator
    would later be shown, not a parallel view that happens to agree today.
    """
    async with connect() as conn:
        run = await conn.fetchrow(
            "SELECT id, app_id, ticket_system, ticket_id, risk_tier, status, policy_commit, "
            "policy_bundle, created_at FROM flow_runs WHERE id = $1",
            run_id,
        )
        if run is None:
            raise RunNotFound(f"no run {run_id}")

        events = await conn.fetch(
            "SELECT seq, kind, node_id, control_mode, payload, occurred_at "
            "FROM flow_events WHERE run_id = $1 ORDER BY seq",
            run_id,
        )
        app_name = await conn.fetchval(
            "SELECT name FROM app_registry WHERE id = $1", run["app_id"]
        )

    rows = [
        {
            "seq": e["seq"],
            "kind": e["kind"],
            "node_id": e["node_id"],
            "control_mode": e["control_mode"],
            "payload": json.loads(e["payload"] or "{}"),
            "occurred_at": e["occurred_at"].isoformat(),
        }
        for e in events
    ]

    def payload_of(kind: str) -> dict[str, Any]:
        """The most recent payload for an event kind, or an empty one."""
        matches = [row["payload"] for row in rows if row["kind"] == kind]
        return matches[-1] if matches else {}

    verdict = payload_of("build_test_verdict")
    isolation = payload_of("sandbox_isolation")

    document: dict[str, Any] = {
        "schema": EVIDENCE_SCHEMA,
        "run_id": str(run["id"]),
        "application": app_name,
        "ticket": {"system": run["ticket_system"], "id": run["ticket_id"]},
        "risk_tier": run["risk_tier"],
        "status": run["status"],
        # Pinned at run start. An approver deciding under one policy and a run executing
        # under another is the failure ADR 0003 §4 exists to make visible.
        "policy_commit": run["policy_commit"],
        "policy_bundle": json.loads(run["policy_bundle"] or "{}"),
        "started_at": run["created_at"].isoformat(),
        "tests": verdict,
        "sandbox_isolation": isolation,
        "caveats": _caveats(run["policy_commit"], verdict, isolation),
        "events": rows,
    }
    return Evidence(run_id=run_id, digest=digest_of(document), document=document)


def _caveats(
    policy_commit: str, verdict: dict[str, Any], isolation: dict[str, Any]
) -> list[str]:
    """Everything about this evidence that is weaker than it looks.

    Stated in the document itself, and therefore inside the digest, so an approver cannot be
    shown a clean page while the qualification lives somewhere they never opened. A caveat an
    approver did not see is not a caveat — it is a disclaimer written for the wrong audience.
    """
    caveats: list[str] = []

    if verdict and verdict.get("source") != "ci":
        # The reason is appended rather than replacing the sentence. "No CI verdict" and "the
        # CI verdict was not sought" are different facts, and an approver deciding what to do
        # about the caveat needs to know which one they are looking at.
        reason = verdict.get("ci_detail")
        caveats.append(
            "The tests were run in KuWarden's own sandbox, not by the project's CI. "
            "The same system produced this change and graded it, so this verdict is not an "
            "independent check (invariant 3)."
            + (f" No verdict came from the project's pipeline: {reason}." if reason else "")
        )
    if not verdict:
        caveats.append("No test verdict was recorded for this run.")
    if isolation.get("state") == "degraded":
        gaps = ", ".join(isolation.get("gaps") or []) or "unspecified"
        caveats.append(
            f"The sandbox ran under weakened isolation ({gaps}). Resource limits the "
            "configuration asked for were not applied by the host."
        )
    if policy_commit.startswith("unpinned:"):
        caveats.append(
            "No policy bundle was pinned for this run, so there is no recorded statement of "
            "which rules authorised it."
        )
    return caveats
