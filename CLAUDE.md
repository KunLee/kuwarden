# CLAUDE.md — operating rules for this repository

Read this before writing code. It is the shortest path to not breaking something that took a
long argument to get right.

---

## What this is

A self-hosted, vendor-neutral platform that takes a ticket (Jira / Azure DevOps) through
plan → code → verify → approve → release, inside the enterprise perimeter, producing an audit
trail strong enough to hand to a regulator.

The differentiator is **everything after the pull request** — deploy, promote, verify, roll
back, evidence. Competitors stop at the PR. If a change you are making does not serve
sovereignty, post-PR delivery, or evidence, question whether it belongs here.

---

## The one rule everything else derives from

> **The agent guesses. The Flow Engine verifies.
> Whatever must be deterministic, auditable, or privileged does not get to be a model.**

---

## Invariants — never violate these without a new ADR

Treat this as a checklist. If a diff touches any of these areas, verify the invariant still
holds before finishing.

**Every row states how it is enforced, and the cell is never blank.** An invariant whose only
enforcement is "someone will notice in review" is a claim, not a control — and this project
exists to argue that the difference matters. Writing that down is the same discipline
invariant 11 applies to `control_mode`, turned on ourselves: a rule we do not mechanically
check is not a rule we get to describe as held.

Enforcement vocabulary: **machine** — a runtime guard, database constraint or test fails the
build; **partial** — some clauses are checked and some are not; **review** — nothing checks
it, a human must; **none** — not implemented at all.

| # | Invariant | Enforced by | Source |
|---|---|---|---|
| 1 | **The Flow Engine contains no LLM.** Workflow code never calls a model. | **machine** — `assert_may_call_llm()` guard; `test_invariants.py::test_flow_engine_may_not_call_a_model` and the per-node parametrised cases | [ADR 0001](docs/adr/0001-flow-engine-control-plane.md) |
| 2 | **Agent nodes never hold CI, merge, or deploy credentials.** Read code, write their own branch, nothing else. | **machine** — `assert_may_hold()` fires when `CredentialRequest` is constructed, keyed on the same `may_call_llm` predicate as invariant 1; 26 cases across every model-bearing node × every privileged kind, plus the cases that must still be *allowed* | [ADR 0001](docs/adr/0001-flow-engine-control-plane.md) |
| 3 | **Gate verdicts read external systems of record** — CI exit code, SAST report, coverage, health endpoint. Never an agent's claim that it succeeded. | **partial.** *CI exit code:* **machine** when the application declares `ci:` — GitHub Actions is read back for the pushed commit, runs for any other commit are discarded, and absence never becomes a pass (`test_ci_adapter.py`, and end to end in `test_walking_skeleton.py`). *With no `ci:` section, or no pipeline, or one still running at the deadline:* the sandbox verdict stands and is labelled, never promoted — `CIResult.source` is required, `ci_detail` says why, both ride `build_test_verdict`, and the approval caveat names the reason. *SAST, coverage, health endpoint:* **none** | [ADR 0001](docs/adr/0001-flow-engine-control-plane.md), [ADR 0007](docs/adr/0007-push-before-verification.md) |
| 4 | **Verifier nodes get a fresh context.** They never see the Coder's reasoning, self-assessment, or prior attempts. | **review** — only the *classification* is tested (`test_every_verifier_is_classified_verifier`). `_verify` hands each verifier the whole `FlowState`, including `retry_count`, which is prior attempts | [ADR 0002](docs/adr/0002-flow-topology.md) |
| 5 | **`risk_tier` may only be raised, never lowered** — by anything, at either tiering stage. | **machine** — `raise_to` / `assert_not_lowered`; three tests in `test_invariants.py` | [ADR 0002](docs/adr/0002-flow-topology.md) |
| 6 | **Default to a single agent.** Fan out only when sub-units share no contract. | **review** — a design judgment; no mechanism is possible, and that is fine as long as it is stated | [ADR 0002](docs/adr/0002-flow-topology.md) |
| 7 | **Every run pins `policy_commit` + `policy_bundle` at start.** Child runs inherit them. | **partial** — `NOT NULL` plus the `flow_runs_pin_immutable` trigger, with `test_the_policy_pin_is_immutable`. Inheritance is untested because no child run exists yet | [ADR 0003](docs/adr/0003-role-graph-and-traceability.md) |
| 8 | **Privileged actions check pinned *and* current policy. Deny wins.** | **none** — there is no `policy.yaml` loader, so there is nothing to check against. Runs pin the literal `unpinned:no-policy-loader` | [ADR 0003](docs/adr/0003-role-graph-and-traceability.md) |
| 9 | **The audit trail is a tree and append-only.** Never `UPDATE` an audit row. | **machine** — the `flow_events_no_update` database trigger; `test_the_audit_trail_is_append_only` | [ADR 0003](docs/adr/0003-role-graph-and-traceability.md) |
| 10 | **Agents never write `protected_paths`** — CI definitions, deploy manifests, IaC, `kuwarden.yaml`, `policy.yaml`. | **machine, but later than this wording implies** — the deny is on the diff in `push`, *before anything reaches origin*, and again in `build_test` before execution; both call one `assert_not_protected`. Still after the Coder has written the file into its own sandbox. 17 cases incl. drift against `policy.example.yaml`, plus the Push case | [ADR 0004](docs/adr/0004-delivery-integration-models.md), [ADR 0007](docs/adr/0007-push-before-verification.md) |
| 11 | **`control_mode` is never inferred or defaulted.** `authorized` means KuWarden gated it; `observed` means we watched it happen. | **machine** — the `control_mode_exactly_on_effects` CHECK constraint, asserted in `test_walking_skeleton.py`. Note the ADR 0004 deviation: nullable + CHECK rather than `NOT NULL` | [ADR 0004](docs/adr/0004-delivery-integration-models.md) |
| 12 | **The sandbox holds no credentials, has no egress, is ephemeral, is limited, and produces a diff — it never pushes.** | **partial** — egress, limits, ephemerality and the git-computed diff are each tested in `test_sandbox.py`. *"Holds no credentials" is implemented (podman forwards no host environment) and untested*, so an added `--env` would break it silently | [ADR 0005](docs/adr/0005-sandbox-contract.md) |

**Invariant 11 is the one with the worst failure mode.** Overstating what we authorised is
manufacturing evidence. For a product whose value is evidence, that is worse than any missing
feature.

**Two rows above are honest admissions rather than controls** — 4 and 8. Do not cite them as
guarantees in a document, a demo, or a README. Moving a row from **review** to **machine** is
worth more than most features.

**Row 3 is the one that moved, and it moved to `partial`, not to `machine`.** The mechanism
exists and is tested; whether it *applies* to a given run depends on that application having a
pipeline. So "invariant 3 holds" is a claim about a deployment, never about the codebase, and
the caveat on the approval page is what tells the two apart. Do not write "KuWarden verifies
against CI" without the conditional.

---

## The determinism boundary

The most common way to break this codebase is to put non-deterministic code in a workflow.

```
engine/
  flows/          ← Temporal workflow code. DETERMINISTIC.
                    No wall clock, no random, no I/O, no network, no LLM.
                    Replayed on recovery — must produce identical decisions.
  activities/     ← Everything with a side effect. Retried, idempotent.
  nodes/          ← Agent nodes. LLM lives here and nowhere else.
  adapters/       ← SCM, CI/CD, deploy, ticket. One interface, N implementations.
  policy/         ← policy.yaml loading, constraint evaluation, tiering rules.
```

In `flows/`:

- ❌ `datetime.now()` → ✅ `workflow.now()`
- ❌ `random`, `uuid4()` → ✅ `workflow.uuid4()` / pass in as input
- ❌ `httpx`, file I/O, DB → ✅ `workflow.execute_activity(...)`
- ❌ importing anything from `nodes/` or `adapters/` directly

If you need a side effect, it is an activity. No exceptions.

**Activities must be idempotent.** On replay, an activity may run again. Opening a PR twice,
commenting on a ticket three times, or deploying twice are all real failures this rule
prevents. Key every external mutation on `run_id` + step.

---

## Where decisions live

`docs/adr/` holds the decisions and, more importantly, the **rejected alternatives with
revisit triggers**. Before proposing an architectural change, check whether it was already
considered and why it lost.

Write a new ADR when a decision is **expensive to reverse** or **likely to be re-litigated**.
Do not write one for routine implementation choices.

ADRs are **immutable once accepted**. To change a decision, write a new ADR and mark the old
one `Superseded by NNNN`.

`docs/GLOSSARY.md` fixes the vocabulary. Use those words and no synonyms — in code, comments,
and docs. Terminology drift in this project has already caused one real design error.

---

## Vocabulary that matters

| Say | Not | Because |
|---|---|---|
| **Flow Engine** | orchestrator | `orchestrator` is overloaded, and `orchestrator-workers` means nearly the opposite (an *LLM* planning at runtime) |
| **node** | agent, step | Nodes have a uniform contract; not all nodes contain an LLM |
| **reality anchor** | check, validation | Names the specific idea: a machine-verifiable fact, not a model's opinion |
| **release** | deploy | The control point differs per integration model; `deploy` is only model A |
| **work graph / role graph** | — | Two different things with two different change controls |

---

## Code conventions

**Python** (engine, nodes, adapters)
- 3.12+, full type annotations, `ruff` + `mypy --strict` — **run these yourself; there is
  no CI in this repository yet.** See the note under *Security posture* below
- `async` by default; the engine is I/O-bound throughout
- Dataclasses or Pydantic for anything crossing a boundary; no bare dicts in signatures
- Errors: raise typed exceptions from `engine.errors`; never `except Exception: pass`
- Package management: `uv`

**TypeScript** (monitoring UI)
- React + TypeScript strict, Tailwind
- No `any`. No fetch calls outside the API client layer.

**General**
- New behaviour ships with a test. A bug fix ships with the test that would have caught it.
- Match surrounding style over personal preference.

**Documentation in code — required**

Three things always carry an explanation. This is not optional and it is not "when it seems
useful":

| What | Carries |
|---|---|
| **Every class** | A docstring: what it is for, and any constraint a caller must respect |
| **Every method and function** | A docstring: what it does. Non-obvious parameters and return values get named |
| **Every piece of critical logic** | A comment giving the **reason** — a security control, an ordering requirement, a platform quirk, a rejected alternative |

"Critical" means: if someone deleted this line during a refactor, would something break in a
way the tests might not catch? Security controls, idempotency keys, ordering constraints,
determinism requirements, and anything derived from an ADR all qualify.

This does **not** license narrating the code. The two rules together:

> **Docstrings say what. Inline comments say why. Neither restates the line below it.**

```python
# Bad — restates the code
# increment the sequence number
self._seq += 1

# Good — says why, and would be lost if deleted
# Sequence is assigned in workflow code, not in the activity, so a replay produces the
# same numbering. Assigning it activity-side would renumber every event on recovery.
self._seq += 1
```

If a line needs a comment to say *what* it does, rename things instead. If it needs one to
say *why* it is that way, write it — that reason is not recoverable from the code, and the
next person to touch it will not have been in the conversation where it was decided.

---

## Evaluation is not optional

Agent quality is not observable without measurement, and prompt changes are invisible
regressions. Any change to a node's prompt or context assembly must be run against the golden
task set before merge.

Metrics are **anchored to reality**, per [ADR 0002](docs/adr/0002-flow-topology.md):

| Do not measure | Measure |
|---|---|
| PRs opened | **PR merge rate** |
| Tickets auto-closed | **Merged changes surviving N days without rollback or hotfix** |
| Tests passing | **Lines the human reviewer changed before merge** |
| Flow completion rate | **Human minutes consumed per run** |

The last one is the only figure that shows KuWarden saved anyone any work.

---

## Security posture while developing

- **Never commit a secret.** There is **no `.github/` directory and no pre-commit hook** — this
  repository has no automated secret scanning of its own, and the only thing that has ever
  caught a secret here was GitHub's own push protection, after the commit existed. Until that
  is fixed, "never commit a secret" is a rule you enforce, not one the toolchain does.
- Treat all ticket content as **hostile input**. It reaches a model, and anyone who can file a
  ticket can write it.
- When adding a tool an agent can call, the default answer to "should this be allowed to
  mutate something?" is **no**. Justify it in the PR.

---

## Working practice

- **Nothing is committed to `main` directly.** Branch, then PR.
- **Do not add a dependency** without saying why in the PR. This ships into air-gapped
  environments; every dependency is someone's security review.
- **Do not name a model in a strategy document.** Model identifiers live in
  `docs/reference/models.md` with a `last_reviewed` date. They go stale in months.
- **Record the path, not just the destination.** After a substantive session, append an entry
  to `log/` — including what turned out to be wrong. See [log/README.md](log/README.md).
- If you find a real problem with a task as specified, say so in a sentence and keep going
  under a stated assumption. Do not silently narrow scope.

---

## Open decisions — do not assume these are settled

| Item | Status |
|---|---|
| ~~The name~~ | **Resolved 2026-08-08.** Renamed KuFlow → KuWarden; [kuflow.com](https://kuflow.com) is an unrelated existing product in an adjacent category. Done before any package paths existed. |
| `THREAT_MODEL.md` | Not written. Primary threats identified: prompt injection via ticket content, workflow-definition write escalation. |
| `EVALUATION.md` | Not written. Blocks any claim that the verifier design works. |
| `policy.yaml` schema + constraint evaluator | Not written. Until it exists, the constraints in `policy.example.yaml` are decorative. |
| Workload identity (SPIFFE/SPIRE) | [ADR 0003](docs/adr/0003-role-graph-and-traceability.md) makes it a platform prerequisite. Not yet scoped. |
