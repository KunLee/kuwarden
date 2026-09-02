# 2026-08-31 — The verifiers said it did not work, and nobody was shown

Previous: [2026-08-30-11](2026-08-30-11-replayed-webhook-revisions.md).

---

## Context

Ticket 50 shipped twice and does not work. [Log 11](2026-08-30-11-replayed-webhook-revisions.md)
blamed a hand-resolved merge of two concurrent runs, and that merge is real and unverified — but
it is not the cause. The user tested before it, and the feature was already missing.

So the pipeline delivered a change that passed the sandbox, was anchored to CI, cleared four
verifiers, was approved by a human, deployed green to production, and does not do what the
ticket asked.

---

## What happened

**The verifiers were right, in writing, and it changed nothing.**

`correctness` returned **passes**, and recorded:

> Header.tsx still renders AdminNavLink separately from the new UserMenu, so admin users get
> two separate admin entry points rather than the requested single consolidated dropdown flow —
> the ticket asked for the profile control itself to offer the admin jump, not to keep the old
> separate icon as well.

> 'Profile details' and 'Manage profile' both navigate to the same '/settings/profile' route, so
> the dropdown doesn't actually offer two distinct actions as implied by the ticket.

That is the defect, described precisely, by the verifier whose job it is, **attached to a
passing verdict**.

`regression_risk` also passed, having written down that changing default prop values in
`dropdown-menu.jsx` would change `inset` semantics for any consumer — which is exactly the
type error that broke the build on a later branch, three call sites that had nothing to do with
the ticket.

`test_evidence` **blocked**. It is declared advisory for this application, so it was overridden.

**What the approver was shown:**

```
verifiers_completed  {"of": 4, "passed": 3, "falsified_by": ["test_evidence"]}
gate_reached         {"tier": "medium", "needed": 1}
```

A count. `engine/evidence.py` did not contain the word *finding*. Seven findings existed in the
audit trail and none of them was on the page. The approval took 157 seconds, which is a
perfectly reasonable amount of time to spend on what was actually displayed.

The file's own docstring had already stated the rule it was breaking:

> A caveat an approver did not see is not a caveat — it is a disclaimer written for the wrong
> audience.

---

## Decisions

- **Every finding reaches the evidence document**, structured, named by the verifier that wrote
  it and by whether it blocked. Carried on `verifiers_completed` from `state.verifications`
  rather than parsed back out of a node's prose notes.
- **Two new caveats, both above the buttons.** A verifier that falsified the change and was not
  permitted to block it is named first — it is the strongest fact on the page. Then a count of
  findings recorded by verifiers that did *not* block, because a passing verdict is not an empty
  one.
- **`EVIDENCE_SCHEMA` 1 → 2.** The document shape is digest input; a change that did not bump it
  could let an old digest match a new document.
- **The findings are never collapsed in the UI.** A finding an approver has to expand is a
  finding they will not read.

---

## What was *not* fixed

**Findings are still disconnected from verdicts.** A verifier can write "this does not do what
the ticket asked" and return `blocks: false`, and nothing checks the two against each other.
This change makes that visible to a human rather than resolving it; whether a verifier should be
able to pass while recording a finding of that severity is a prompt-and-schema question, and it
wants evidence from `EVALUATION.md` before anyone tunes it by feel.

**Verifier findings still do not feed back into the Coder** — log 09 recorded this, and ticket 50
is what it costs. Two runs, from the same base, missed the same intent, and neither could see
what the other had been told. The manual substitute this time was pasting the findings into the
work item by hand ([ticket-50-supplement.md](ticket-50-supplement.md)).

---

## Open

- **The UI does not typecheck.** `ui/src/pages/RunDetail.tsx:73` calls `api.run`, which does not
  exist in `ui/src/api.ts`. Pre-existing on this branch and unrelated to this change; Vite strips
  types without checking them, so the app runs and `tsc --noEmit` fails. Nothing in CI appears to
  catch it.
- **Ticket 50 is the first real evaluation case**, and the most valuable kind: every gate green,
  the outcome wrong. `EVALUATION.md`'s results table is still empty, and this is the row it
  should start with.

---

## Artefacts

**New** — `log/2026-08-31-12-findings-never-reached-the-approver.md`,
`log/ticket-50-supplement.md`

**Changed** — `engine/evidence.py`, `engine/flows/delivery.py`,
`ui/src/components/ApprovalGate.tsx`, `ui/src/types.ts`, `tests/test_workbench_api.py`.

Suite: **389 passed, 1 skipped**. `ruff` and `mypy --strict` clean; the frontend typechecks
apart from the pre-existing `RunDetail.tsx` error above.
