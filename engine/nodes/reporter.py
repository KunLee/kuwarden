"""Reporter — `deterministic`.

Posts the outcome and its evidence back to the ticket. Terminal on both the success and the
compensation path, so a run always says what became of it.
"""

from __future__ import annotations

from engine.adapters.factory import ticket_adapter
from engine.nodes.base import context, node
from engine.state import FlowState, NodeClass


@node(node_id="reporter", name="Reporter", node_class=NodeClass.DETERMINISTIC)
async def reporter(state: FlowState) -> FlowState:
    ctx = context()
    if not ctx.config.triggers:
        return state

    trigger = next(
        (t for t in ctx.config.triggers if t.provider == state.ticket.system),
        ctx.config.triggers[0],
    )
    adapter = ticket_adapter(trigger, ctx.broker, transport=ctx.transport)
    await adapter.comment(trigger.ref(state.ticket.id), _body(state))
    return state


def _body(state: FlowState) -> str:
    pull_requests = [a.uri for a in state.artifacts if a.kind == "pull_request"]
    lines = [
        f"KuWarden run `{state.run_id}` — risk tier **{state.risk_tier}**.",
        "",
    ]
    if pull_requests:
        lines += ["Pull request: " + ", ".join(pull_requests), ""]
    if state.verifications:
        lines.append("Verification:")
        lines += [
            f"- {v.verifier}: {'passed' if v.passed else 'FAILED'}" for v in state.verifications
        ]
        lines.append("")
    if state.approvals:
        lines.append("Approvals:")
        lines += [
            f"- {a.principal}: {'approved' if a.approved else 'rejected'}"
            for a in state.approvals
        ]
        lines.append("")
    lines.append(f"Policy commit `{state.policy_commit}`.")
    return "\n".join(lines)
