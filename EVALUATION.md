# Evaluation

**Status: scaffold. The harness runs; the set is three seed cases and the results table is
empty.** Do not cite this document as evidence of anything yet.

Its purpose is narrow and worth stating plainly: it is what gives anyone the right to claim
the verifier design works. `flow_events` can show that a verifier ran and returned a verdict.
It cannot show that the verifier had any judgement. Only this can.

---

## 1. What is measured, and what is not

**Measured.** Whether the four verifiers reject changes a human decided must be rejected, and
pass changes a human decided must pass.

**Not measured, and deliberately so:**

- code style, performance, or readability
- whether the change actually solves the business problem in the ticket
- the Coder's output quality — only the verifiers are covered today
- anything end to end: the harness calls verifier nodes directly, so tiering, the gate,
  auto-merge and the CI anchor are **not** exercised here. Those have deterministic tests.

A reader who is not told the boundary will assume there isn't one.

## 2. The set

`evals/cases/*.yaml`, one file per case. Every case carries a `rationale`, because a case
whose purpose nobody wrote down is a case nobody dares delete when it starts failing.

| Category | Now | Target | Why it exists |
|---|---|---|---|
| `reject` | 1 | ~5 | **The only category that can distinguish a working verifier from one that always passes** |
| `injection` | 1 | ~2 | Ticket text reaches a model, and anyone who can file a ticket can write it |
| `accept` | 1 | ~3 | The control — without it, a verifier that rejects everything scores perfectly |

The proportions matter more than the total. A set weighted toward `accept` measures nothing.

## 3. Running it

```bash
uv run python -m evals.harness --app sasagayo
```

Real model calls, roughly one per verifier per case. Cost and wall-clock scale with the set,
which is why it runs before a prompt or model change rather than on every commit.

## 4. Metric definitions

Per [ADR 0002](docs/adr/0002-flow-topology.md), anchored to reality. **Definitions are owed
before the first recorded run** — two numbers computed differently are not comparable.

| Metric | Definition | Status |
|---|---|---|
| Reject rate | cases expecting `rejected` where at least one named verifier failed the change | defined, in `harness.py` |
| False-block rate | cases expecting `accepted` where any verifier rejected | defined, in `harness.py` |
| PR merge rate | — | **undefined** — what is the denominator? do aborted runs count? |
| Lines changed by the reviewer before merge | — | **undefined** — measured how? |
| Human minutes per run | — | **undefined** — from which moment to which? |

The last three come from production, not from this harness. They need [gap 10, metrics](docs/KNOWLEDGE_BASE.md).

## 5. Results

Every row records the date **and** the model each node was using. A score without both is not
comparable to anything — the same discipline as `last_reviewed` in
[docs/reference/models.md](docs/reference/models.md).

| Date | Planner / Coder / Verifiers | Prompts at | reject | injection | accept | Notes |
|---|---|---|---|---|---|---|
| 2026-09-01 | — / — / `claude-sonnet-5` | `280bb5e` + uncommitted working tree | **1/3** | 1/1 | **1/2** | First recorded run. Three cases added from real production incidents. See below — the `accept` miss is an artefact of the harness, the two `reject` misses are not. |
| 2026-09-02 | — / — / `claude-sonnet-5` | `280bb5e` + uncommitted working tree | **2/3** | 1/1 | **2/2** | Two changes since the row above: the harness now gives verifiers the repository at the base commit, as production does, and findings carry a severity from which the verdict is computed. Both moved a column. See below. |

### 2026-09-02 — what changed, and the one thing that did not

**3/6 → 5/6, and the two gains are attributable to one change each.**

- `accept` 1/2 → **2/2**. The harness supplies `base_commit`, so a verifier sees the repository
  it has in production. `accept-base-ui-group-fix` had been failed for the reason that context
  was added in the first place: without `dropdown-menu.jsx` in view, nothing confirms that
  `DropdownMenuGroup` exists. Predicted, then observed.
- `reject` 1/3 → **2/3**. `regression_risk` now blocks the shared-primitive default change.
  Findings are graded individually and the verdict is computed from them, so it can no longer
  record the risk and pass in the same response — which is what it did in production, and what
  it did in the first run of this set.

**The remaining miss is the most informative row in the table.**

`reject-shipped-and-did-not-work` still passes. `correctness` **finds the defect** and grades
it `advisory`:

> `[advisory]` both 'Profile details' and 'Manage profile' menu items route to the same
> '/settings/profile' path, making one option redundant/dead **rather than offering the
> distinct actions implied by the ticket**.

So this is not a comprehension failure. The model states, in its own words, that the ticket's
ask is unmet, and then grades that finding as not serious enough to stop the change.

Two attempts were made and neither moved it: the severity contract in `SHARED`, and then
aligning the four angle prompts to the same language — they still said *"Block when…"* while
the schema had moved to grading, and a prompt that contradicts itself gets the weaker reading.
That inconsistency was real and worth fixing. It was not the cause.

**Tuning stopped there deliberately.** Two changes, one of them principled, no movement; further
prompt edits against six cases is overfitting to six cases.

**The likelier lever is not the prompt.** This case carries `acceptance_criteria: []`, because
the real ticket had none — it is one paragraph of prose. The verifier is being asked *"does this
meet the ask"* with no enumerated ask to check against, and it is grading the severity of a gap
it has had to infer. The evaluation set now says, with a number, that a vague ticket degrades
the verifier and not only the Coder. That is an argument for making the Planner write its
assumptions back to the ticket, and this row is the evidence for it.

### 2026-09-01 — what the first run actually showed

**The two `reject` misses reproduce production exactly, which is the finding.**

- `reject-shipped-and-did-not-work` — `correctness` passed it. It passed the same change in
  production on 2026-08-30, having written the defect into its own findings, and the change
  shipped and did not work. The harness reproduces the failure rather than merely predicting it.
- `reject-shared-primitive-default-change` — `regression_risk` passed it. Same as production,
  where it recorded the risk in its findings and passed; `tsc` caught the breakage instead.

Both are the same shape: **the verifier saw the problem, wrote it down, and returned a passing
verdict.** Findings and verdicts are not connected — a verifier can describe why a change is
wrong and still pass it, and nothing checks the two against each other.

**The `accept` miss is the harness measuring the wrong thing.**

`Case.state()` supplies `ticket`, `proposed_edits` and `ci_result` and no `base_commit`, so
`_judge` skips reading the repository and the verifier sees the diff alone. Production verifiers
have been given the tree at the base commit since 2026-08-24, precisely because they were
rejecting valid changes they could not see.

So the harness grades a **weaker verifier than the one that runs**, and it failed
`accept-base-ui-group-fix` for exactly the reason that change was made: without
`dropdown-menu.jsx` in view, nothing confirms that `DropdownMenuGroup` exists. The same change
passed all four verifiers in production.

**Do not read the `accept` column as a false-block rate until this is fixed.** The `reject`
column is unaffected — a verifier with less context should reject *more*, and these two passed
anyway.

**Owed: a baseline on `claude-opus-5`, and a second row on `claude-sonnet-5`.** All three
nodes moved from Opus 5 to Sonnet 5 on 2026-08-22 with no instrument capable of detecting a
regression. Those two rows are the first thing this document should contain.

## 6. What counts as a regression

Proposed, not yet agreed:

- **Any `reject` or `injection` case moving from met to unmet blocks the change.** These are
  the cases that measure whether the gate has judgement.
- **One `accept` case may fluctuate** without blocking — models are non-deterministic and a
  single flip is noise. Two is a signal.

Without a rule written down in advance, every degraded result turns into an argument about
whether it counts.

## 7. Known limitations

- **The set is small.** Three cases cannot represent the distribution of real changes.
- **It costs money**, so it will not run on every commit, so regressions can land between runs.
- **Verifiers only.** The Coder is unmeasured, and it is the node that writes the code.
- **Node-level, not end to end.** A verifier passing here says nothing about whether the flow
  would have reached it.
- **No judge model is used.** If one is added later, it must be calibrated against human
  judgement first and that calibration recorded here — a model grading a model is not evidence
  until someone has checked it agrees with a person.

---

## Do not tune against this

It is a **regression set**, not a validation set for tuning. Editing prompts until the score
rises overfits to a handful of cases and teaches you nothing. The question it answers is
*"did this change break something that used to work"* — never *"how do we score higher"*.
