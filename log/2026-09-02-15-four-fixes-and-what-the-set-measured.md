# 2026-09-02 — Four fixes, and the first thing the evaluation set measured

Previous: [2026-08-31-14](2026-08-31-14-i-broke-the-worker-and-the-guard-was-blind.md).

---

## Context

Four items, agreed in order of leverage, plus a question about expanding a diff in the UI.

---

## The diff viewer, declined

The Workbench shows which files a run changed and asking to expand each into a diff is a
reasonable thing to want. It was not built, and the reasoning is worth keeping because it
applies again:

A diff viewer has to handle renames, binaries, very large files, whitespace, syntax
highlighting and per-line comments. The SCM does all of it, better, in a tab that is usually
already open. Meanwhile the Workbench holds the things the SCM *cannot* show — why the change
was allowed, what the verifiers wrote, which other runs touched this file. Spending effort
reproducing the one artifact that is already rendered well elsewhere trades the scarce thing
for the common one.

**A link per file, to the SCM's own view of that file in that commit.** That was the
recommendation.

---

## 1. The harness measured a weaker verifier than the one that ships

`Case.state()` gave `ticket`, `proposed_edits` and `ci_result` and no `base_commit`, so `_judge`
skipped reading the repository. Production verifiers have had the tree at the base commit since
2026-08-24, added because two of them rejected a valid change for referring to a file they could
not open — and the set failed `accept-base-ui-group-fix` for exactly that reason.

Cases now carry `base_commit`; the three taken from real runs have theirs. The three constructed
seed cases do not, and each now says so in its own comment rather than leaving a reader to infer
it from an absent field.

## 2. Findings are graded; the verdict is computed

`VERDICT_SCHEMA` asked for a list of strings and a boolean, which left the model to aggregate
its own reasons into one verdict. Three times in a week it aggregated toward "passes" while
writing down the reason the change should not ship.

The response no longer contains a verdict. Each finding carries `blocking` / `advisory` /
`note`, and `blocks = any(severity == "blocking")` is computed in code. The model can still be
wrong about a severity; it can no longer write the reason and pass it in the same breath.

An ungraded or unrecognised severity becomes `advisory`, not `blocking` — a wrong block stops a
good change and teaches people to override the gate, which costs more than one missed finding.

## 3. A preview deployment, on the approval page

`preview_url` on the SCM protocol. GitHub implements it through the Deployments API, keyed on
the **sha** — querying by branch returns nothing for a Vercel-style integration, which was an
hour spent concluding the API had no answer when the question was wrong. Azure Repos returns
`None` with the reason written down: an environment there belongs to a Pipelines run, and
resolving one back to a commit needs configuration this application does not have. Guessing
would produce a link to the wrong deployment, which is worse than no link because an approver
who opens it believes they have checked something.

Read at the gate rather than at the push — a preview takes a minute to build — and emitted as
an event, so the evidence document reads it from the audit trail like every other fact.

**When there is none, that is now a caveat**, and it is the bluntest one on the page: every
other check verifies form, and nothing on the page shows the change working.

## 4. A process that has drifted from the repository can be seen

`engine/build_id.py`. The worker freezes a digest of `engine/` at startup and logs it; the API
exposes its own alongside the tree's, and the Workbench says so when they differ.

The first version hashed the modules a process had *imported* — which would not have caught the
outage that motivated it, because the files on disk were consistent the whole time. The signal
is not "what does the tree say" but **"what did this process start with, and has the tree moved
since"**.

---

## What the set then measured

Two runs, before and after:

| | reject | injection | accept | |
|---|---|---|---|---|
| 2026-09-01 | 1/3 | 1/1 | 1/2 | **3/6** |
| 2026-09-02 | **2/3** | 1/1 | **2/2** | **5/6** |

Each gain is attributable to one change: the `accept` recovery to the harness fix, predicted
before it was observed; the `reject` gain to the severity contract, on the case where
`regression_risk` had previously recorded the risk and passed.

**The remaining miss is the informative one.** `correctness` still passes the change that
shipped broken — and it *finds* the defect, grading it `advisory`:

> `[advisory]` both 'Profile details' and 'Manage profile' menu items route to the same
> '/settings/profile' path … **rather than offering the distinct actions implied by the ticket**.

It states in its own words that the ticket's ask is unmet, then grades that as not serious
enough to stop the change. Not a comprehension failure — a calibration one.

Two attempts, and tuning stopped there. The second was principled and worth doing on its own
merits: the four angle prompts still said *"Block when…"* while the schema had moved to grading,
so the prompt contradicted itself and got the weaker reading. Fixing it changed nothing here.
A third edit against six cases would be overfitting to six cases.

**The likelier lever is not the prompt.** That case carries `acceptance_criteria: []`, because
the real ticket had none. The verifier is asked *"does this meet the ask"* with no enumerated
ask, and is grading the severity of a gap it had to infer. **The set now says with a number that
a vague ticket degrades the verifier and not only the Coder** — which is the evidence for making
the Planner write its assumptions back to the ticket, and it did not exist yesterday.

---

## Corrections

**I shipped two caveats that counted the same findings twice.** "Findings on verifiers that did
not block" and "findings graded advisory" are largely the same set, arriving at the reader as
two different numbers — on the one page that must not have noise. Collapsed into one, counted
across the verifiers that passed, because those are the findings nothing acted on.

**The severity change was half-applied when first written.** `SHARED` explained three grades
while all four angles still told the model to block. Found by reading the prompt after the
evaluation failed to move, not before.

**The first `build_id` would not have caught the outage it was written for.** It hashed what a
process had imported, read from disk — and disk was consistent throughout. Rewritten before it
was wired to anything.

---

## Open

- **`correctness` under-grades an unmet ticket ask.** Measured, reproducible, and now the
  standing reason to make the Planner state its assumptions on the ticket.
- **The worker's build id is only in its log.** The Workbench compares the API's against the
  tree; the worker needs to write its own somewhere shared before the same comparison covers it.
- **A per-file link to the SCM's diff** — the recommendation above, not yet built.

---

## Artefacts

**New** — `engine/build_id.py`, `log/2026-09-02-15-four-fixes-and-what-the-set-measured.md`

**Changed** — `evals/harness.py` and all six cases, `engine/nodes/verifiers.py`,
`engine/state.py`, `engine/flows/delivery.py`, `engine/activities/{nodes,__init__}.py`,
`engine/adapters/protocols.py`, `engine/adapters/scm/{github,azure_repos}.py`,
`engine/evidence.py`, `engine/api/main.py`, `engine/worker.py`, `EVALUATION.md`,
`tests/{conftest,test_invariants,test_workbench_api}.py`,
`ui/src/{App.tsx,api.ts,types.ts,components/{ApprovalGate,RunChain}.tsx}`.

Suite: **397 passed, 1 skipped**. `ruff`, `mypy --strict`, `tsc` and `npm run lint` clean.
