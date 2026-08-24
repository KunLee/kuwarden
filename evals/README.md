# Evaluation harness

The 326 tests in `tests/` cover deterministic code. Every one of them uses `MockTransport` or
`FakePlatform`, so **replacing all four verifier prompts with "always pass" would leave them
green**. The parts of this system that contain a model have no coverage at all.

This directory is that coverage.

## What it is, and is not

| | `tests/` | `evals/` |
|---|---|---|
| Under test | deterministic code | the four verifiers, and later the Coder |
| Model calls | none | **real ones — this costs money** |
| Asserts | exact output | a *property* of the verdict |
| Result | pass / fail | a **rate**, recorded with the date and model |
| When | every commit | before a prompt or model change, and periodically |

**Node-level, not end-to-end.** A verifier reads `ticket`, `proposed_edits` and `ci_result`
and nothing else, so a case can construct those directly. No Temporal, no sandbox, no
repository, no Coder — one model call per verifier per case.

## The rule that makes this worth building

> A verifier that examines nothing and always passes produces output **identical** to one that
> works — as long as you only ever show it good changes.

So the set is weighted toward cases that **must be rejected**. A suite of happy-path cases
proves nothing whatsoever. `accept` cases exist only as a control: to catch a verifier that has
become so suspicious it blocks everything.

## Do not tune against it

This is a **regression set**, not a validation set for tuning. Editing prompts until the score
goes up overfits to a dozen cases and teaches you nothing. It answers *"did this change break
something that used to work"* — never *"how do I get a higher number"*.

## Running it

```bash
uv run python -m evals.harness            # every case
uv run python -m evals.harness --category reject
```

Results go in [`../EVALUATION.md`](../EVALUATION.md), **with the date and the model each node
was using**. A number without those two is not comparable to anything.

## Adding a case

One YAML file per case in `cases/`. Schema in `harness.py`. Every case carries a `rationale`
— a case whose purpose nobody recorded is a case nobody dares delete when it starts failing.

**The set is fixed between comparisons.** Adding cases is fine; when you do, re-run the
baseline and note in `EVALUATION.md` that the denominator changed.
