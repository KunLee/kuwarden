"""① Triage & Risk Router — `deterministic`, with an advisory LLM.

Stage one of two. The facts final tiering depends on — which paths the diff touches, whether
it reaches `migrations/`, how large it is — do not exist yet, because there is no diff. What
this stage produces is provisional: admission control and budget allocation, and it may be
wrong.

Tiering is rules-first. An advisory model may contribute, but only to raise a tier. There is
no model here yet, and the rules below are the whole of it.
"""

from __future__ import annotations

from engine.adapters.factory import ticket_adapter
from engine.errors import PolicyDenied
from engine.nodes.base import context, node
from engine.policy.tiering import raise_to
from engine.state import FlowState, NodeClass, RiskTier


@node(node_id="triage", name="Triage & Risk Router", node_class=NodeClass.DETERMINISTIC)
async def triage(state: FlowState) -> FlowState:
    ctx = context()
    if not ctx.config.triggers:
        raise PolicyDenied(f"{ctx.config.name} declares no triggers")
    trigger = next(
        (t for t in ctx.config.triggers if t.provider == state.ticket.system),
        ctx.config.triggers[0],
    )

    adapter = ticket_adapter(trigger, ctx.broker, transport=ctx.transport)
    ticket = await adapter.fetch(trigger.ref(state.ticket.id))
    state.ticket = ticket

    # Admission control. A ticket outside the declared scope is refused here rather than
    # discovered three nodes later, and refusal is a human's problem, not a retry.
    if trigger.label and trigger.label not in ticket.labels:
        raise PolicyDenied(
            f"{ticket.id} does not carry the {trigger.label!r} label "
            f"that {ctx.config.name} requires"
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
    if any(label in ctx.config.risk.high_labels for label in ticket.labels):
        tier = raise_to(tier, "high")

    state.risk_tier = raise_to(state.risk_tier, tier)
    state.provisional_risk_tier = state.risk_tier
    state.budget_cents_allowed = (
        state.budget_cents_allowed or ctx.config.budget_cents_per_run
    )
    return state
