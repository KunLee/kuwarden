"""⑧ Abort / Rollback / Cleanup — `deterministic`.

Compensation is driven from outside the thing that crashed, because a crashed process cannot
clean up after itself: the branch is orphaned, the ticket is stuck in progress, a partial
deployment is unrolled back.

**This node also carries the reason.** It is the last node a rejected run reaches and the
first one anyone opens when asking "why did this stop?". The `aborting` event records a
one-line reason; the verifier findings that produced it were reaching nobody. They are already
on the state, so this node reads them and writes them into its own record.

**Deleting is destroying evidence, so this node deletes narrowly.** The branch is removed only
when no pull request was opened against it — if one was, a human is already involved and
removing the branch under them is not tidying, it is taking away the thing they were asked to
look at. Where nothing was opened, nobody outside KuWarden ever saw the branch, and the commit
sha stays in the append-only record either way.

**Nothing here may raise.** This node runs *because* something already went wrong. A failure
during cleanup that propagated would replace a diagnosable original error with a confusing
second one, and the run would end reporting the wrong cause. Every step is attempted, every
failure is recorded on the state, and the node returns.

What is deliberately **not** done: the ticket is not transitioned back. Moving somebody's work
item between states is a governance act, not cleanup — the Reporter comments on it instead,
and a human decides what the ticket should say.
"""

from __future__ import annotations

import logging

from engine.adapters.factory import scm_adapter
from engine.errors import KuWardenError
from engine.nodes import notes
from engine.nodes.base import context, node
from engine.state import FlowState, NodeClass

log = logging.getLogger(__name__)


def _why(state: FlowState) -> tuple[str, list[notes.Section]]:
    """The reason this run is being compensated, as far as the state can say.

    Returns a one-line summary and the sections that explain it. Verifier findings are marked
    untrusted for the same reason the Coder's prompt is: they are model output quoting a diff
    that came from a ticket anyone can file, and a reader should see them as evidence to check
    rather than as the system's own conclusion.
    """
    rejected = [v for v in state.verifications if not v.passed]
    if not rejected:
        # Compensation also runs for a node failure or an approver's rejection. Neither leaves
        # a falsified verification, and claiming one would be worse than saying nothing.
        return "Run aborted — see the preceding events for the cause", []

    # Split by what each verifier was permitted to do, not by what it concluded. An advisory
    # verifier's findings still belong in this record — suppressing them would be the "saved a
    # model call and destroyed the evidence" trade the toggle exists to avoid — but it did not
    # cause the rejection, and the summary line is what a reader takes away.
    #
    # Empty `rejected_by` means the abort had some other cause and every falsification is
    # reported as it was before, rather than none of them.
    blocking = [v for v in rejected if not state.rejected_by or v.verifier in state.rejected_by]
    advisory = [v for v in rejected if v not in blocking]

    named = ", ".join(v.verifier for v in blocking) or "an earlier step"
    sections: list[notes.Section] = [
        notes.fields(
            "Which verifiers falsified the change",
            [(v.verifier, f"{len(v.findings)} finding(s) — blocked the change") for v in blocking]
            + [
                (v.verifier, f"{len(v.findings)} finding(s) — advisory, could not block")
                for v in advisory
            ]
            + [(v.verifier, "passed") for v in state.verifications if v.passed],
        )
    ]
    for verification in rejected:
        for index, finding in enumerate(verification.findings, start=1):
            sections.append(
                notes.text(
                    f"{verification.verifier} — finding {index}", finding, untrusted=True
                )
            )
    return f"Rejected by {named}", sections


@node(node_id="compensate", name="Abort / Rollback / Cleanup", node_class=NodeClass.DETERMINISTIC)
async def compensate(state: FlowState) -> FlowState:
    ctx = context()
    summary, sections = _why(state)

    def record(cleanup: str | None) -> FlowState:
        """One exit point, so every path records the reason and not only the tidy ones."""
        if cleanup is not None:
            state.cleanup = cleanup
        state.notes = notes.compose(
            summary + (f" — {state.cleanup}" if state.cleanup else ""),
            *sections,
            notes.fields(
                "Cleanup",
                [
                    ("Branch", state.branch or "none pushed"),
                    ("Head commit", state.head_commit or "—"),
                    ("Action", state.cleanup or "nothing to tidy"),
                ],
            ),
        )
        return state

    if not state.branch or not state.head_commit:
        # Nothing was pushed, so there is nothing on anyone's remote to tidy.
        return record(None)

    if any(artifact.kind == "pull_request" for artifact in state.artifacts):
        return record("branch kept: a pull request was opened against it")

    repo = ctx.config.primary
    try:
        removed = await scm_adapter(repo, ctx.broker, transport=ctx.transport).delete_branch(
            repo.ref(), state.branch
        )
    except (KuWardenError, Exception) as exc:  # noqa: BLE001 - cleanup never fails the run
        # Recorded rather than raised. A cleanup failure is worth knowing about and is not
        # worth losing the original error over.
        log.warning("run %s: could not delete %s: %s", state.run_id, state.branch, exc)
        return record(f"branch {state.branch} could not be deleted: {exc}")

    return record(
        f"branch {state.branch} deleted at {state.head_commit[:8]}"
        if removed
        else f"branch {state.branch} was already gone"
    )
