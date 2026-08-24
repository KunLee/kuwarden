"""What a node read, decided and produced — in a form the audit trail can carry.

Until this existed, `node_completed` carried an empty payload. The run record could say *that*
Triage admitted a ticket and *that* the Planner produced a plan; it could not say which trigger
matched, what the ticket actually said, which admission checks were evaluated, what went into
the prompt, or what came back. Every one of those facts existed for the duration of one
activity and was then discarded.

That is a gap in the product's central claim. "The agent guesses, the Flow Engine verifies" is
only inspectable if the record says what was verified against what.

**Notes are evidence, not logging.** They are written into `flow_events`, which is append-only
(invariant 9) and never expires, unlike the Temporal history the diagnostics panel reads. So:

- **Never put a credential, token or key in a note.** Nothing here is redacted on the way out,
  and the trail cannot be edited afterwards. `FlowState` excludes secrets for the same reason
  (ADR 0001) — this is the same rule one layer up.
- **Ticket and model text is untrusted.** Mark it with `untrusted=True` so the reader is told
  whose words they are looking at. It reaches a model and it reaches a UI.
- **Bounded.** Every text block is capped. A repository listing or a 200 KB ticket body
  multiplied by every attempt of every run is a database, not a record.

Three renderable kinds, deliberately few — the Workbench renders exactly these and a fourth
would mean a note that displays as raw JSON:

`fields`  label/value pairs — what something was.
`checks`  label, required, found, verdict — a decision, with the rule it was decided against.
`text`    a block, shown preformatted — a prompt, a plan, a body.
"""

from __future__ import annotations

from typing import Any

#: Long enough for a plan, a ticket body or a Planner prompt in full; short enough that the
#: Coder's repository context is clipped rather than copied into Postgres on every attempt.
MAX_TEXT = 8_000

Section = dict[str, Any]
Notes = dict[str, Any]


def compose(summary: str, *sections: Section | None) -> Notes:
    """Assemble one node's notes. `None` sections drop out, so callers can inline conditions."""
    return {"summary": summary, "sections": [s for s in sections if s is not None]}


def fields(title: str, rows: list[tuple[str, Any]]) -> Section:
    """Label/value pairs, rendered in the order given."""
    return {"title": title, "kind": "fields", "rows": [[k, _scalar(v)] for k, v in rows]}


def checks(title: str, rows: list[tuple[str, Any, Any, bool]]) -> Section:
    """Decisions, each as (label, required, found, ok).

    `required` and `found` are both recorded even when a check passes. "Story points 3, limit
    5" is the fact; "story points ok" is a claim that cannot be re-derived by a reader who
    disagrees.
    """
    return {
        "title": title,
        "kind": "checks",
        "rows": [
            {"label": label, "required": _scalar(required), "found": _scalar(found), "ok": ok}
            for label, required, found, ok in rows
        ],
    }


def text(title: str, body: str, *, untrusted: bool = False, tail: bool = False) -> Section:
    """A preformatted block, clipped to `MAX_TEXT`.

    `untrusted` marks text written by whoever filed the ticket or returned by a model. The
    reader is told; nothing is stripped, because a note that quietly altered what a model was
    sent would be evidence of something that did not happen.

    `tail` keeps the end instead of the beginning. Test output is the case: a suite that fails
    prints its failure last, and the first 8 KB of a passing preamble is the one part nobody
    needs. Which end was kept travels with the note, so a reader is never guessing.
    """
    return {
        "title": title,
        "kind": "text",
        "body": body[-MAX_TEXT:] if tail else body[:MAX_TEXT],
        "untrusted": untrusted,
        # Stated rather than left to a trailing ellipsis. A reader must be able to tell a plan
        # that ended from a plan that was cut off.
        "truncated": len(body) > MAX_TEXT,
        "kept": "end" if tail else "start",
        "full_length": len(body),
    }


def _scalar(value: Any) -> str:
    """Everything reaches the record as a string, so rendering never depends on JSON types."""
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value) if value else "—"
    return str(value)
