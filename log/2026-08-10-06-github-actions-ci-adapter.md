# 2026-08-10 — the CI adapter, and what "the tests passed" is allowed to mean

Previous: [2026-08-10-05](2026-08-10-05-push-before-verification.md).

---

## Context

Entry 05 moved the push inside the loop and said plainly that the ADR had "paid its cost and
collected none of its benefit" until a CI adapter existed. This is the collection.

Invariant 3 has been in deviation since the project started. Every previous session handled
it the same honest way — label it, carry it into the audit trail, put it in front of the
approver — and every previous session was right to, because the alternative was pretending.
This is the first one where the underlying fact could actually change.

---

## What happened

### The interesting design work was all about what *not* to do

The adapter itself is a listing endpoint and a poll loop. Three refusals took the thinking:

**Read-only, with no `trigger` and no `rerun` on the interface.** A pipeline KuWarden can
start is a pipeline KuWarden can influence, and the entire value of CI as a reality anchor is
that it cannot. The push is what causes CI to run. `CredentialKind.CI_READ` is new and
deliberately separate from the privileged `CI_TRIGGER` for the same reason.

**A verdict is read for exactly one commit.** `?head_sha=` filters server-side, and the result
is filtered again client-side. This is the failure that would have been easiest to ship and
hardest to notice: a green pipeline belonging to attempt 1, read while grading attempt 2,
satisfies every other assertion anyone would think to write. `_for_commit` exists solely to
make it impossible, and `test_a_verdict_for_another_commit_is_discarded` is the test I would
keep if I had to delete all the others.

**Absence is never a pass.** No `ci:` section, no pipeline, a pipeline still running when the
wait expired — each returns *no verdict* with a reason, and the sandbox result stands with its
caveat. Three states, one of which had to be resisted: it would have been very easy for "we
found no failing run" to become "nothing failed".

### Two waits, not one

`grace_s` bounds *nothing has appeared yet*; `wait_s` bounds *it appeared and is still going*.
Collapsing them into one number is wrong in both directions. Too short and every repository
looks like it has no CI, because a workflow run takes seconds to be created after a push. Too
long and a genuinely CI-less repository stalls every attempt for the full timeout.

### The asymmetry: CI is only consulted when the sandbox passed

A gate only ever opens on a pass, so the pass is the claim that needs an external anchor. A
sandbox failure already sends the Coder round again, and waiting fifteen minutes for CI to
agree delays the retry and changes nothing.

This started as an efficiency argument and turned out to be the correctness one. Worth keeping
straight when someone proposes making it symmetric for tidiness.

### Both graders travel

`sandbox_result` is kept alongside `ci_result`, and `ci_detail` records how the verdict was
reached — on success as well as failure. The audit event carries all three. A run whose CI was
never consulted must not read, a year later, like one whose CI passed.

---

## Decisions

No ADR. This implements what [ADR 0007](../docs/adr/0007-push-before-verification.md) already
recorded as owed, and the fallback behaviour — sandbox verdict plus caveat when CI says
nothing — is a direct application of the labelling strategy that has been the project's answer
to this since session 01.

**If that gets re-litigated it should become one.** The live version of the argument is
whether a run should *fail* when it cannot obtain an independent verdict, rather than proceed
on a labelled sandbox one. Today's answer is proceed, because refusing would make KuWarden
unusable on any repository without a pipeline, and a labelled weakness beats no product. That
is a governance judgement wearing implementation clothes, and the day someone disagrees it
needs a record rather than an argument.

---

## Corrections

**A test whose name did not match what it tested.** `test_a_failing_sandbox_is_not_waited_on`
actually asserted the *empty change* path — a different early exit that reaches the same
conclusion for a different reason. Its own docstring admitted this, which made it worse rather
than better: the note explained the mismatch instead of fixing it. Split into two tests, one
with a genuinely failing sandbox stub.

The general shape is worth remembering: **a docstring explaining why a test does not test its
name is a defect report, not documentation.**

**Invariant 3 moved to `partial`, not to `machine`, and the CLAUDE.md paragraph naming the
honest admissions had to move with it.** The first draft of that row read as though the
invariant now held. It does not — it holds for a run whose application has a pipeline that
produced a verdict, which is a claim about a *deployment*, not about the codebase. The row
also still says nothing anchors SAST, coverage, or health, which are three of the four systems
of record the invariant names. One of four is progress and is not compliance.

**`_run_at_gate` was a fixture that could only tell one story.** The evidence tests all built
their run with a hard-coded `source: "sandbox"` verdict, so "the caveat appears" was tested
and "the caveat disappears" could not be. A caveat that cannot be removed by fixing the
underlying fact is decoration. The fixture is now parametrised on the verdict payload, and
`test_a_ci_verdict_removes_the_independence_caveat` is the test that would fail if `_caveats`
were ever simplified into always warning.

---

## Open

1. **Azure Pipelines.** The interface is provider-neutral and the aggregation rules are
   shared, so this is one module — but it is not written, and Azure DevOps is the flagship
   ticket system, so the gap is in an odd place.
2. **SAST, coverage, health.** Invariant 3 names four systems of record. One is read.
3. **Branch cleanup on abort**, carried over from entry 05 and now slightly worse: a rejected
   run leaves a branch *and* whatever pipeline runs it triggered.
4. **Operator guidance on pipeline triggers**, owed by ADR 0007 and now urgent rather than
   theoretical — turning on `ci:` is what makes a customer's pipeline run against agent code.
5. Unmoved: the four verifier nodes are still stubs, no webhook receiver, no `policy.yaml`
   loader, the `control_mode` ADR 0004 deviation on `main`.

---

## Artefacts

**New**
- `engine/adapters/ci/__init__.py` — protocol, aggregation rules, the wait loop
- `engine/adapters/ci/github_actions.py`
- `tests/test_ci_adapter.py`

**Changed**
- `engine/nodes/build_test.py` — two graders; `_anchor_to_ci`
- `engine/config.py` — `CiConfig`, `_ci`
- `engine/adapters/factory.py` — `ci_adapter`
- `engine/adapters/credentials.py` — `CI_READ`
- `engine/state.py` — `sandbox_result`, `ci_detail`
- `engine/flows/delivery.py` — both verdicts on `build_test_verdict`
- `engine/evidence.py` — the caveat now says *why* no CI verdict exists
- `tests/conftest.py` — a fake pipeline with knobs for pending / failing / absent
- `tests/test_workbench_api.py` — `_run_at_gate` parametrised; the caveat-disappears test
- `tests/test_walking_skeleton.py` — asserts `source == "ci"` for the pushed commit
- `CLAUDE.md` (invariant 3 → `partial`; the honest-admissions paragraph), `ARCHITECTURE.md`,
  `kuwarden.example.yaml`, `docs/END_TO_END.md`, `docs/KNOWLEDGE_BASE.md`
