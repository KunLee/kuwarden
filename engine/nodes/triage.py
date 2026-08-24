"""① Triage & Risk Router — `deterministic`, with an advisory LLM.

Stage one of two. The facts final tiering depends on — which paths the diff touches, whether
it reaches `migrations/`, how large it is — do not exist yet, because there is no diff. What
this stage produces is provisional: admission control and budget allocation, and it may be
wrong.

Tiering is rules-first. An advisory model may contribute, but only to raise a tier. There is
no model here yet, and the rules below are the whole of it.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from engine.adapters.factory import ticket_adapter
from engine.errors import PolicyDenied
from engine.nodes import notes
from engine.nodes.base import context, node
from engine.policy.application import assert_configured_for
from engine.policy.tiering import raise_to
from engine.state import FlowState, NodeClass, RiskTier

log = logging.getLogger(__name__)


@node(node_id="triage", name="Triage & Risk Router", node_class=NodeClass.DETERMINISTIC)
async def triage(state: FlowState) -> FlowState:
    ctx = context()
    # First, before a credential is resolved or a ticket is fetched. A worker serves exactly
    # one application's configuration, so a run for another would read this application's
    # repository list and merge policy while holding the other's tokens — and would do it
    # silently. See engine/policy/application.py.
    assert_configured_for(ctx.config.name, state.app_name)

    if not ctx.config.triggers:
        raise PolicyDenied(f"{ctx.config.name} declares no triggers")
    #: Which trigger, and why this one. An application may declare several; the run's record
    #: should not leave a reader to re-derive which set of rules was applied.
    matched = [t for t in ctx.config.triggers if t.provider == state.ticket.system]
    trigger = matched[0] if matched else ctx.config.triggers[0]
    selection = (
        f"matched on provider {state.ticket.system!r}"
        if matched
        else f"no trigger declares provider {state.ticket.system!r}; fell back to the first"
    )

    adapter = ticket_adapter(trigger, ctx.broker, transport=ctx.transport)
    ticket = await adapter.fetch(trigger.ref(state.ticket.id))
    state.ticket = ticket

    # Each check records what was required and what was found, passing or not. A trail that
    # says "label ok" cannot be re-checked by a reader who thinks the rule was wrong; one that
    # says "required 'kuwarden-auto', found ['bug', 'kuwarden-auto']" can.
    admission: list[tuple[str, object, object, bool]] = []

    # Admission control. A ticket outside the declared scope is refused here rather than
    # discovered three nodes later, and refusal is a human's problem, not a retry.
    admission.append(
        (
            "Required label",
            trigger.label or "none required",
            ticket.labels or "no labels",
            not trigger.label or trigger.label in ticket.labels,
        )
    )
    if trigger.label and trigger.label not in ticket.labels:
        raise PolicyDenied(
            f"{ticket.id} does not carry the {trigger.label!r} label "
            f"that {ctx.config.name} requires"
        )
    # The state is what makes starting work a *deliberate* act. A ticket save fires on every
    # field change — a reassignment, a typo fix — and admitting on that infers an intention
    # from activity. Moving a ticket into a named state is somebody saying "go", and this is
    # the check that reads it. Case-insensitive because platforms title-case inconsistently
    # and "ready for agent" versus "Ready for Agent" is not a governance distinction.
    admission.append(
        (
            "Ready state",
            trigger.ready_state or "any state admitted",
            ticket.state or "unknown",
            not trigger.ready_state
            or (ticket.state or "").casefold() == trigger.ready_state.casefold(),
        )
    )
    if trigger.ready_state and (ticket.state or "").casefold() != trigger.ready_state.casefold():
        raise PolicyDenied(
            f"{ticket.id} is in state {ticket.state or 'unknown'!r}; "
            f"{ctx.config.name} admits tickets in {trigger.ready_state!r}"
        )
    admission.append(
        (
            "Story points",
            f"at most {trigger.max_story_points}"
            if trigger.max_story_points is not None
            else "no ceiling",
            ticket.story_points if ticket.story_points is not None else "not estimated",
            trigger.max_story_points is None
            or ticket.story_points is None
            or ticket.story_points <= trigger.max_story_points,
        )
    )
    if (
        trigger.max_story_points is not None
        and ticket.story_points is not None
        and ticket.story_points > trigger.max_story_points
    ):
        raise PolicyDenied(
            f"{ticket.id} is {ticket.story_points} points; "
            f"{ctx.config.name} auto-handles at most {trigger.max_story_points}"
        )

    tier: RiskTier = ctx.config.default_risk_tier
    escalating = [label for label in ticket.labels if label in ctx.config.risk.high_labels]
    if escalating:
        tier = raise_to(tier, "high")

    state.risk_tier = raise_to(state.risk_tier, tier)
    state.provisional_risk_tier = state.risk_tier
    state.budget_cents_allowed = state.budget_cents_allowed or ctx.config.budget_cents_per_run

    criteria = "\n".join(f"{i}. {c}" for i, c in enumerate(ticket.acceptance_criteria, 1))
    state.notes = notes.compose(
        f"Admitted {ticket.id} — provisional risk tier {state.risk_tier}",
        notes.fields(
            "Trigger applied",
            [
                ("Selection", selection),
                ("Provider", trigger.provider),
                ("Project", trigger.project),
                # Where it was read from. A run whose ticket came from the wrong organisation
                # is a question somebody eventually asks, and the answer must be in the record
                # rather than in whatever kuwarden.yaml says today.
                ("Organisation", trigger.organisation),
                ("Site", trigger.site),
                ("Triggers declared", len(ctx.config.triggers)),
            ],
        ),
        notes.fields(
            "Ticket as read from the tracker",
            [
                ("Id", ticket.id),
                ("Title", ticket.title),
                ("State", ticket.state),
                ("Labels", ticket.labels),
                ("Story points", ticket.story_points),
                ("Acceptance criteria", f"{len(ticket.acceptance_criteria)} stated"),
                ("Body", f"{len(ticket.body)} characters"),
            ],
        ),
        notes.checks("Admission control", admission),
        # The words the model will be given, recorded before it sees them. Marked untrusted:
        # anyone who can file a ticket wrote this, and the reader of an audit trail should
        # never be in doubt about which text in front of them is ours.
        notes.text("Ticket body — untrusted input", ticket.body, untrusted=True)
        if ticket.body
        else None,
        notes.text("Acceptance criteria — untrusted input", criteria, untrusted=True)
        if criteria
        else None,
        notes.fields(
            "Risk tiering — stage one of two",
            [
                ("Application default", ctx.config.default_risk_tier),
                ("Labels that escalate", ctx.config.risk.high_labels or "none configured"),
                ("Escalating labels found", escalating or "none"),
                ("Provisional tier", state.risk_tier),
                # Named for what it is. Final tiering runs after the diff exists, and a reader
                # who takes this for the authoritative tier would be reading a number that the
                # gate may not have used.
                ("Authoritative", "no — final tiering runs after the Coder loop"),
                ("Budget allocated", f"{state.budget_cents_allowed} cents"),
            ],
        ),
    )

    await _acknowledge(adapter, trigger, state)
    return state


async def _acknowledge(adapter: Any, trigger: Any, state: FlowState) -> None:
    """Tell the ticket it was picked up, once.

    Until this existed, a ticket went silent between being moved into the ready state and the
    run finishing — no confirmation the trigger had even fired. The natural response to that
    silence is to save the ticket again, which does nothing, or to conclude it is broken.

    **It never fails the run.** A ticket system being briefly unreachable must not reject work
    that is otherwise fine. Admission has already been decided above; this is a courtesy, and
    turning a courtesy into a delivery outage would be a worse bug than the silence.

    **It is posted once.** Activities retry, and a ticket API offers no idempotency token, so
    the comment carries a marker naming the run and existing comments are read back first.
    Without that, a retried Triage leaves "picked up" on the ticket two or three times —
    exactly the failure CLAUDE.md names for external mutations.

    **It carries no diff and no findings.** A board is readable by more people than the
    Workbench, so this says *that* a run started and where to look, never what the code does.
    """
    marker = f"kuwarden-run: {state.run_id}"
    base = os.environ.get("KUWARDEN_BASE_URL", "http://localhost:5173").rstrip("/")
    try:
        ref = trigger.ref(state.ticket.id)
        if any(marker in body for body in await adapter.comments(ref)):
            return
        await adapter.comment(
            ref,
            "KuWarden picked this up and started a run.\n\n"
            f"Provisional risk tier: {state.risk_tier} "
            "(the authoritative tier is decided after the change exists).\n"
            f"Follow it here: {base}/runs/{state.run_id}\n\n"
            f"{marker}",
        )
    except Exception as exc:  # noqa: BLE001 - a notification must never fail a delivery
        log.warning("could not acknowledge ticket %s: %s", state.ticket.id, exc)
