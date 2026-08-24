# 2026-08-15 — CI for this repository, and turning invariant 12 on ourselves

Previous: [2026-08-11-07](2026-08-11-07-first-real-run-end-to-end.md).

---

## Context

Asked what was worth building next. The honest answer was not a feature.

Twelve invariants in CLAUDE.md, most of them marked **machine**, and the machine is
`tests/test_invariants.py`. There was no `.github/` directory. So the enforcement for the
rules this project exists to argue about ran when somebody remembered to run it, which is
exactly the thing the table's own vocabulary calls **review** rather than **machine**.

Worth stating plainly because it is the uncomfortable version: we had written a document
insisting that a rule nobody mechanically checks is not a rule you get to describe as held,
and then described twelve rules as held.

An early confusion, resolved before any code: *whose* CI. The pipeline KuWarden reads back
under invariant 3 belongs to the application under delivery and is named in its
`kuwarden.yaml` — the client creates it, and nothing in this session touches it. What was
missing was KuWarden's CI for KuWarden's own source. Two different things that share a word.

---

## What happened

### The baseline was already broken, which was the argument

Before writing the workflow, ran the checks it was going to run. `ruff` clean; `mypy --strict`
**failed** — `tests/test_ci_adapter.py:36` constructed `SandboxCapabilities()` with no
arguments, and the dataclass has five required fields.

Nobody introduced that carelessly. It is what happens to any check that depends on a person
remembering, over enough commits. Fixing it was two minutes; the useful part is that it was
sitting there, in a repository whose README-level claim is that unverified controls drift.

The fix is not `SandboxCapabilities(...)` with defaults added — that dataclass has no defaults
*on purpose* (ADR 0005: a sandbox reporting a limit it does not apply is the failure the whole
type exists to prevent). Filled the five fields explicitly instead.

### Invariant 12 needed two tests, not one

The untested clause was *"the sandbox holds no credentials"*. `podman.py` passes exactly one
`--env`, `HOME=/tmp`, and the comment explains why. The regression to defend against is
someone adding `--env=SOMETHING` while debugging and leaving it.

`test_sandbox.py` is module-level skipped without podman, and its docstring is right that a
mocked sandbox proves only that the mock is isolated. But a test that skips on the CI runner
is not enforcement on the CI runner. Both, therefore:

- **`test_sandbox.py`** — set a variable in the pytest process, run `env` in the container,
  assert it is absent. Proves podman behaves as assumed. Skips without podman.
- **`test_invariants.py`** — a `PodmanSandbox` subclass that captures argv and runs nothing,
  asserting the only environment-bearing flag is `HOME`. Needs no container runtime, so it
  runs everywhere. Also catches `--env-host` and `--env-file`, which are the flags that would
  do the most damage and the easiest to add without thinking.

**Both were then verified by breaking the property**, not by watching them pass: injected
`--env=KUWARDEN_SCM_TOKEN=...` into `podman.py`, confirmed both tests fail, reverted. A test
written for a regression that has never been observed to fail is a guess about its own
behaviour.

### The database, and a false green that nearly shipped

Locally the suite showed `1 failed, 222 passed, 25 skipped, 19 errors` — all PostgreSQL. The
cause was mundane (another project's Postgres occupying 5432), but it exposed something that
mattered: **some test modules skip themselves when the database is absent and others error.**

A CI job with a service container that silently failed to start would therefore go *green*
with the credential-store and Workbench tests quietly skipped. That is the same shape as the
failure invariant 11 is written against — a control reported as exercised that was not.

So the workflow runs `python -m engine.db migrate` before `pytest`, which both applies
migrations and fails the job loudly if the database is unreachable. CI asserts its own
preconditions rather than trusting its own result.

Verified for real rather than by inspection: started a throwaway `postgres:17` with the exact
CI configuration on a spare port, ran the whole suite against it — **256 passed, 13 skipped,
zero failures.** The 32 tests that were erroring locally pass. The workflow is proven, not
hoped for.

### Small decisions, recorded because they will look arbitrary later

**`POSTGRES_HOST_AUTH_METHOD: trust` rather than a password.** The container is reachable only
from its own job and dies with it. The alternative is a literal password committed to the
repository, and a fake credential in a committed file is indistinguishable *to a reader* from
a real one somebody forgot to remove. Committing a string that looks like a secret, in the
same commit that adds secret scanning, teaches the wrong reflex.

**Gitleaks as a pinned, checksum-verified binary rather than the official Action.** The
digest is recorded in the workflow rather than fetched at run time: a checksums file
downloaded next to the artifact it attests to proves only that the two agree, and whoever can
replace one can replace the other. The pin in the repository is the out-of-band half. Version
8.30.1, digest `551f6fc8…`.

**`--redact` everywhere.** A scanner that prints the secret it found into a CI log has
widened the exposure it was added to narrow.

**The `detect` subcommand is deprecated** as of gitleaks 8.19; `git` and `dir` replace it.
Checked rather than assumed, because a wrong subcommand would have failed open on the first
run and looked like a pass.

---

## Decisions

- KuWarden's own CI is three jobs — engine (ruff, mypy, migrate, pytest against a Postgres
  service), ui (tsc, oxlint), secrets (gitleaks over full history). No ADR: this is a routine
  implementation choice, not one that is expensive to reverse.
- Secret scanning is enforced in CI and **advisory** at pre-commit. `.githooks/pre-commit`
  does nothing until someone runs `git config core.hooksPath .githooks`, because git does not
  version `.git/hooks`. CLAUDE.md now says this in those words.
- Invariant 12 moves **partial → machine**. Invariant 3's SAST and coverage cells are
  untouched and remain **none**.

---

## Corrections

- **The invariant table overstated itself, and this entry's author wrote most of it.** Twelve
  rows described as mechanically enforced, with no mechanism to run them on anything but a
  developer's laptop when they thought to. The rows were not lies — the tests exist and pass —
  but "enforced by machine" was doing work the repository could not back.
- **`mypy --strict` had been failing for some unknown number of commits.** Nobody knew,
  because nothing checked.
- **The first plan was one sandbox test.** That would have added a test which skips on exactly
  the machine where enforcement matters — the appearance of closing the gap, in the place the
  gap is worst. Splitting it in two was the correction.

---

## Open

- **Temporal is not in CI, so `test_walking_skeleton.py` still skips there — 13 tests.** That
  is where invariants 3, 10 and 11 are asserted end to end, including invariant 11's
  `control_mode_exactly_on_effects` CHECK constraint. CLAUDE.md calls invariant 11 the one
  with the worst failure mode. **It is still not mechanically checked by CI.** A
  `temporalio/auto-setup` service container is the obvious next step and was deliberately not
  attempted here, because it could not be validated locally in this session and a flaky
  invariant job gets disabled, which is worse than an absent one.
- **`ui/package-lock.json` is excluded by the blanket `package-lock.json` rule in
  `.gitignore`** (added for `docs/diagrams/`). So the ui job runs `npm install` and resolves
  fresh versions every time. This directly contradicts the reasoning that put `uv.lock` under
  version control — air-gapped environments, where a non-reproducible resolve is a blocker.
  Narrowing the ignore rule and committing the lockfile is a one-line change; not made here
  because committing a lockfile is the repository owner's call.
- **The ui job does not fail on lint warnings.** Five exist today (fast-refresh exports,
  exhaustive-deps). `oxlint --deny-warnings` would gate them; turning it on in the same commit
  that introduces the job would have made CI red on arrival for reasons unrelated to it.
- **Actions are pinned to major tags, not commit SHAs.** `actions/checkout@v4`,
  `actions/setup-node@v4`, `astral-sh/setup-uv@v5`. SHA pinning is the stronger posture for a
  project with this threat model. Not done here because fabricating a digest I had not
  verified would be worse than a tag.
- Invariant 8 still has **no** enforcement, and there is still no `policy.yaml` loader. This
  session did not touch it.

---

## Artefacts

Created:

- `.github/workflows/ci.yml` — three jobs, read-only token, concurrency-cancelled
- `.gitleaks.toml` — upstream rules extended; allowlist deliberately limited to lockfiles
- `.githooks/pre-commit` — opt-in, degrades loudly when gitleaks is absent
- `log/2026-08-15-08-repository-ci-and-secret-scanning.md` — this entry

Changed:

- `tests/test_invariants.py` — invariant 12 section, argv assertion, no podman required
- `tests/test_sandbox.py` — the real-container half of the same property
- `tests/test_ci_adapter.py` — the `mypy --strict` failure CI would have caught
- `CLAUDE.md` — invariant 12 row to **machine**; the "no CI" and "no secret scanning"
  statements replaced with what is now true, including what the pre-commit hook is not
- `ROADMAP.md` — Phase 0 CI ticked; secret scanning marked half done, Semgrep still absent
