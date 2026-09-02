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
#:
#: 2 — verifier findings and the advisory override. See `_caveats`.
#: 3 — the preview deployment, and the caveat when there is none.
EVIDENCE_SCHEMA = 3


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
    # What the verifiers actually wrote, not how many of them passed. A count is the one
    # summary that cannot be acted on: three verifiers can pass while recording, between
    # them, the reason the change does not work.
    verifications = payload_of("verifiers_completed").get("verifications") or []
    # A running deployment of the exact commit, when the platform published one. A link,
    # never a verdict — see `preview_url` on the SCM protocol.
    preview = payload_of("preview_published").get("url") or ""
    overridden = payload_of("verifier_overridden")

    # The tier from the trail, falling back to the column only when final tiering has not run.
    #
    # `flow_runs.risk_tier` is written once, at run start, and never again — so it holds the
    # *provisional* tier that intake guessed from the ticket's labels. Reading it here showed
    # an approver "risk tier: low" on a page that was demanding two signatures because the
    # diff had raised the change to high. The evidence document is what a decision is bound
    # to; a document that understates the tier understates the reason the reader is being
    # asked at all.
    #
    # This is also what the docstring above already promised: the audit trail is the record,
    # and a parallel view that happens to agree today is not the same thing.
    final = payload_of("risk_tier_final")
    risk_tier = final.get("tier") or run["risk_tier"]

    document: dict[str, Any] = {
        "schema": EVIDENCE_SCHEMA,
        "run_id": str(run["id"]),
        "application": app_name,
        "ticket": {"system": run["ticket_system"], "id": run["ticket_id"]},
        "risk_tier": risk_tier,
        # Both, when they differ. "It is high" and "intake guessed low and the diff
        # raised it" are different facts, and the second is the one that tells an
        # approver why a change that read as routine is in front of them.
        "provisional_risk_tier": final.get("provisional") or run["risk_tier"],
        "risk_tier_reason": final.get("reason") or "final tiering did not run",
        "status": run["status"],
        # Pinned at run start. An approver deciding under one policy and a run executing
        # under another is the failure ADR 0003 §4 exists to make visible.
        "policy_commit": run["policy_commit"],
        "policy_bundle": json.loads(run["policy_bundle"] or "{}"),
        "started_at": run["created_at"].isoformat(),
        "tests": verdict,
        "sandbox_isolation": isolation,
        # Every finding, named by the verifier that wrote it and by whether it blocked. A
        # passing verdict is not an empty one, and the difference is the approver's to weigh.
        "verifications": verifications,
        "preview_url": preview,
        "caveats": _caveats(
            run["policy_commit"], verdict, isolation, verifications, overridden, preview
        ),
        "events": rows,
    }
    return Evidence(run_id=run_id, digest=digest_of(document), document=document)


def _caveats(
    policy_commit: str,
    verdict: dict[str, Any],
    isolation: dict[str, Any],
    verifications: list[dict[str, Any]] | None = None,
    overridden: dict[str, Any] | None = None,
    preview: str = "",
) -> list[str]:
    """Everything about this evidence that is weaker than it looks.

    Stated in the document itself, and therefore inside the digest, so an approver cannot be
    shown a clean page while the qualification lives somewhere they never opened. A caveat an
    approver did not see is not a caveat — it is a disclaimer written for the wrong audience.
    """
    caveats: list[str] = []

    # First, because it is the strongest thing on the page: a review that refused this change
    # and was configured not to be able to stop it. Recorded as a count until ticket 50, where
    # the disarmed verifier was the only one that objected.
    for name in (overridden or {}).get("advisory") or []:
        caveats.append(
            f"{name} falsified this change and was not permitted to block it, because this "
            "application declares it advisory. Its findings are on this page and were not "
            "acted on by the gate."
        )

    # Then the findings of the verifiers that *passed*. This is the case ticket 50 shipped
    # through: `correctness` returned a passing verdict and wrote, in the same breath, that
    # the change did not do what the ticket asked. A verdict is a judgement; the findings are
    # what it was a judgement about, and only one of the two reached the approver.
    # One caveat, not two. An earlier version counted "findings on verifiers that did not
    # block" and "findings graded advisory" separately, which are largely the same findings
    # arriving at the reader as two different numbers — noise on the one page that must not
    # have any.
    #
    # Counted across verifiers that passed, because those are the findings nothing acted on:
    # a blocking verifier's advisories travel with a verdict that already stopped the change.
    quiet = [
        f
        for v in (verifications or [])
        if not v.get("blocks")
        for f in (v.get("graded") or [{"detail": d} for d in (v.get("findings") or [])])
    ]
    if quiet:
        caveats.append(
            f"{len(quiet)} finding(s) were recorded by verifiers that did not stop this change "
            "— each judged, by the verifier that wrote it, not serious enough to block. A "
            "passing verdict is not an empty one, that judgement is theirs, and this page is "
            "where it can be overruled."
        )

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
    # Last, and phrased as a limit rather than a warning. Every other caveat here says a
    # control was weaker than it looks; this one says a whole dimension has no control at all.
    if not preview:
        caveats.append(
            "No running deployment of this commit was found, so nothing on this page shows "
            "the change working. Every check above verifies form — lint, types, the build, and "
            "four models reading a diff — and none of them can tell whether the change does "
            "what the ticket asked."
        )

    if policy_commit.startswith("unpinned:"):
        caveats.append(
            "No policy bundle was pinned for this run, so there is no recorded statement of "
            "which rules authorised it."
        )
    return caveats
