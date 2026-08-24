"""Reporter — `deterministic`.

Posts the outcome and its evidence back to the ticket. Terminal on both the success and the
compensation path, so a run always says what became of it.
"""

from __future__ import annotations

from engine.adapters.factory import ticket_adapter
from engine.nodes import notes
from engine.nodes.base import context, node
from engine.state import FlowState, NodeClass


@node(node_id="reporter", name="Reporter", node_class=NodeClass.DETERMINISTIC)
async def reporter(state: FlowState) -> FlowState:
    ctx = context()
    if not ctx.config.triggers:
        state.notes = notes.compose(
            "Nothing was posted — the application declares no trigger to post back to",
            notes.fields(
                "Why",
                [
                    ("Triggers declared", 0),
                    # A run that reported nowhere looks identical to one that reported
                    # successfully unless the record says which happened.
                    ("Outcome", "the ticket was not told what became of this run"),
                ],
            ),
        )
        return state

    trigger = next(
        (t for t in ctx.config.triggers if t.provider == state.ticket.system),
        ctx.config.triggers[0],
    )
    adapter = ticket_adapter(trigger, ctx.broker, transport=ctx.transport)
    body = _body(state)
    await adapter.comment(trigger.ref(state.ticket.id), body)

    state.notes = notes.compose(
        f"Posted the outcome to {trigger.provider} {state.ticket.id}",
        notes.fields(
            "Where it was posted",
            [
                ("Provider", trigger.provider),
                ("Project", trigger.project),
                ("Ticket", state.ticket.id),
                # Reporter is terminal on both paths, so its presence proves nothing about the
                # run's outcome. Saying that here stops the node being read as a success marker.
                ("Runs on", "every path — success and compensation alike"),
            ],
        ),
        notes.text("Comment, as posted", body),
    )
    return state


def _body(state: FlowState) -> str:
    pull_requests = [a.uri for a in state.artifacts if a.kind == "pull_request"]
    # The tier *and* what settled it. A ticket describing a theme switch that comes back
    # "risk tier high" reads as the system being arbitrary; the same line naming the rule and
    # the file reads as the system doing its job. Escalations are the decisions people are
    # most likely to dispute, so they are the ones that must arrive explained.
    raised = (
        state.provisional_risk_tier
        and state.provisional_risk_tier != state.risk_tier
        and state.risk_tier_reason
    )
    tier = (
        f"risk tier **{state.risk_tier}**, raised from {state.provisional_risk_tier} "
        f"because {state.risk_tier_reason}"
        if raised
        else f"risk tier **{state.risk_tier}**"
        + (f" — {state.risk_tier_reason}" if state.risk_tier_reason else "")
    )
    lines = [
        f"KuWarden run `{state.run_id}` — {tier}.",
        "",
    ]
    if pull_requests:
        lines += ["Pull request: " + ", ".join(pull_requests), ""]
    if state.verifications:
        lines.append("Verification:")
        for v in state.verifications:
            if v.passed:
                lines.append(f"- {v.verifier}: passed")
            elif v.verifier in state.advisory_objections:
                # The distinction that matters most and is easiest to lose. "objected" and
                # "objected and stopped the change" are different events, and a reader who
                # sees FAILED beside a merged change concludes the gate is decorative —
                # rather than that somebody deliberately declared this verifier advisory.
                lines.append(
                    f"- {v.verifier}: objected, but is configured as advisory "
                    "and could not block"
                )
            else:
                lines.append(f"- {v.verifier}: FAILED — this stopped the change")
        lines.append("")
    if state.approvals:
        lines.append("Approvals:")
        lines += [
            f"- {a.principal}: {'approved' if a.approved else 'rejected'}" for a in state.approvals
        ]
        lines.append("")
    lines.append(f"Policy commit `{state.policy_commit}`.")
    return "\n".join(lines)
