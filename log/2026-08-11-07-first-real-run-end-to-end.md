# 2026-08-11 — the first real run, and the ten defects it found

Previous: [2026-08-10-06](2026-08-10-06-github-actions-ci-adapter.md).

---

## Context

One work item, one repository, one operator, start to finish: Azure DevOps work item 28 →
GitHub `KunLee/sasagayo` → branch → pull request → comment back on the ticket.

It succeeded on the fourth attempt. The three failures, and everything found on the way, are
the substance of this entry — because **almost none of them were findable without running it**.
The suite was green throughout. 235 tests passed while the product could not complete a run.

---

## What running it found that the tests could not

In order of how badly each one misled the operator.

### 1. The audit trail did not record failures

The worst of them. `_node_step` emitted `node_started`, called the activity, and emitted
`node_completed`. When the activity raised, **nothing was recorded at all** — the trail showed
a start and then silence, and the reader had to infer a failure from a missing row and could
never learn its reason. The top-level `except Exception` compensated without emitting either.

For a product whose entire claim is an audit trail strong enough for a regulator, "the run
failed and the record does not say why" is the one gap that cannot be waved through.

Now `node_failed` and `run_failed`, carrying the error type and message, ordered so the record
reads as things happened: what broke, then what was done about it.

### 2. A truncated response was reported as a formatting problem

The Coder ran for 4.5 minutes and failed with `schema was requested but the response was not
JSON`. The operator went to look at the model's output format. The real cause was
`max_tokens`, and **the check for it was written directly below the parse, where it could
never fire** — a response cut off mid-JSON fails to parse first.

Ordering fixed, and the message now names the cause and the fix. `LLMOutputTruncated` is
non-retryable for the same reason `LLMAuthError` is: the same prompt under the same cap
truncates at the same place, and each attempt was 4.5 minutes and a full charge.

The underlying cause was real too — the Coder's schema returns *whole file contents*, so its
output budget scales with the size of the files, not with the length of an answer. 8192 was
never going to be enough.

### 3. Every button in the Workbench was invisible

`bg-[--color-accent]` is Tailwind **v3** shorthand for a CSS variable. The project is on v4,
where it compiles to nothing. 87 occurrences across 12 files: buttons had no background,
inputs no border, body text no colour. The sign-in button was white text on transparent over
a near-white canvas.

Found with browser devtools reading `getComputedStyle`, not with a test — the UI has `tsc`
and `oxlint` and neither can see that a class produced no CSS.

### 4. "I clicked Probe and nothing happened"

Two independent causes, and the combination was worse than either. The error banner rendered
near the top of a long page; Probe sits at the bottom. And the first fix — `sticky top-2` —
collided with the header, which is also `sticky z-10`. Now pinned to the bottom, where nothing
has to yield.

### 5. A lapsed session looked like a broken feature

The diagnostics panel reported `Execution detail unavailable: not signed in` in small grey
text, and the operator reasonably concluded the new feature was broken. The app kept rendering
a signed-in shell while every request was being refused. Any 401 now clears the session and
returns the user to sign-in, handled once in the API client.

### 6. The flow diagram said the verifiers had not run

They had. `_verify` runs them with `record=False` and brackets the fan-out with
`verifiers_started` / `verifiers_completed`, which carry no `node_id` — so a diagram deriving
state per node found nothing and drew "not reached" on a stage that plainly ran.

The component's own docstring promises the picture cannot disagree with the evidence. It did.

Also in the same diagram: labels came from `event.node_id` for visited nodes and from the
topology for unvisited ones, so half the boxes read `triage` and half read `③ Coder` — one
diagram in two vocabularies.

### 7. Probe reported a control point over a deployment that does not exist

`deployment_protection = "the /environments endpoint replied"`. A repository with zero
environments answers 200 with an empty list, so model C was reported achievable on a
repository with nothing to pause. The existing test asserted this behaviour, so the bug was
encoded twice.

Also: a 403 on branch protection was annotated with "a 404 here means…", which sends a reader
looking at a 403 to check the wrong thing entirely.

### 8. And then I built the same overstatement myself

To catch a missing write grant *before* a run, I read GitHub's `permissions.push` and reported
"may write a branch". It returned `true` for a token that had just failed with 403.

**`permissions` is the account's role on the repository, not the token's grant.** A repository
owner sees `push: true` with a fine-grained token scoped to Contents: Read. I very nearly
shipped a green tick over the exact failure it was built to prevent — after spending the day
telling the operator that overstating a check is the cardinal sin here.

Now three-state: `push: false` is definitive and useful, `push: true` is `None` with the reason
written out. The trap is in the docstring, because the next person will reach for the same
field.

### 9–10. Two errors that sent the operator to debug the wrong system

An **empty repository** returns `default_branch` but 404s on the ref, which read as a bad token.
A **403 on a write** says only "Resource not accessible by personal access token", which is true
and names none of a dozen permission toggles. Both now say what is actually wrong, and the 403
explains the asymmetry that makes it confusing — Contents: Read is enough for every node up to
and including the Coder, so everything succeeds until the push.

---

## Built this session

- **CI adapter finished** — GitHub Actions, read-only, verdict anchored to the pushed commit
- **`ready_state`** — a ticket must be in a named workflow state to be admitted. The trigger
  that scales: a ticket *save* fires on every field change, and admitting on that spends a
  model budget on typo fixes. Works on the manual path too, so it earns its keep before any
  webhook exists
- **Compensation** — see below
- **Control point is changeable**, with an append-only `app_changes` log
- **Connection checks** — SCM, ticket and model credentials, each reported separately
- **BPMN-shaped run diagram** with per-node popups, hand-drawn: the topology is fixed
  (ADR 0002), so a graph library would be several hundred kilobytes and a security review for
  an automatic layout that cannot draw the bounded cycle
- **`/api/runs/{id}/diagnostics`** — stack traces from Temporal's history, deliberately a
  separate endpoint from the audit trail because a stack trace cannot be removed from an
  append-only table
- **Node logging** at one choke point, every line carrying the run id
- **`scripts/dev-up.ps1`** — preflight plus the three processes

### Compensation, and why it deletes narrowly

`compensate` was `return state`. It now deletes the branch it pushed — **unless a pull request
was opened against it**.

That condition is the whole design. Deleting is destroying evidence: the commit sha survives in
the append-only record, but the content does not. Where no pull request exists, nobody outside
KuWarden ever saw the branch. Where one does, a human is already involved and removing the
branch under them is not tidying, it is taking away the thing they were asked to look at.

Nothing in that node raises. It runs *because* something already went wrong, and a failure
during cleanup that propagated would replace a diagnosable original error with a confusing
second one.

The ticket is deliberately **not** transitioned back. Moving somebody's work item between
states is a governance act, not cleanup.

---

## Decided, and not built

**No email to the ticket's author or assignee.** The operator proposed it, then proposed the
better alternative himself: comment on the ticket and let the board's own subscription deliver
it. Two reasons and the second is decisive — a second "who gets told" source will drift from
the first, and **Jira hides `emailAddress` behind privacy settings on most instances**, so the
implementation would fail silently exactly where it was needed.

---

## The lesson worth keeping

**A green suite and a working product are different claims**, and this session is the sharpest
example the project has produced. 235 tests passed while the Workbench rendered invisible
buttons, the audit trail recorded no failures, and a truncated response was reported as a
formatting error.

Every one of those was found by an operator trying to do the thing, not by a test. That is not
an argument for fewer tests — the tests caught nothing *because* they were testing the layers
that were correct. It is an argument that **"nobody has run it end to end" is a specific,
nameable gap**, and that it stays open until somebody does.

The 2026-08-09 entry already recorded a version of this: *"the tests pass" and "a person can
run it" are different claims*. It was written about a missing entry point. It applies at least
as strongly to a product that has one.

---

## Where to pick up

The full list is now in [docs/KNOWLEDGE_BASE.md](../docs/KNOWLEDGE_BASE.md) under *Not built*,
ordered by what it costs to be missing. The top three:

1. **The four verifiers are stubs.** Nothing has reviewed any diff KuWarden has produced.
2. **`policy.yaml` has no loader.** No org-level defaults, so every application repeats itself.
3. **Budgets are recorded and never enforced.** `cents_per_run` is decorative.

---

## Artefacts

**New** — `engine/adapters/ci/`, `engine/db/migrations/005_app_changes.sql`,
`006_trigger_ready_state.sql`, `ui/src/components/FlowGraph.tsx`, `docs/TROUBLESHOOTING.md`,
`scripts/dev-up.ps1`, `scripts/dev-down.ps1`, `scripts/preflight.py`,
`scripts/reset-dev-data.py`, `tests/test_ci_adapter.py`

**Substantially changed** — `engine/nodes/compensate.py`, `engine/nodes/build_test.py`,
`engine/flows/delivery.py`, `engine/adapters/scm/github.py`,
`engine/adapters/llm/anthropic_api.py`, `engine/api/main.py`, `ui/src/` (87 Tailwind
replacements across 12 files), `CLAUDE.md`, `docs/KNOWLEDGE_BASE.md`
