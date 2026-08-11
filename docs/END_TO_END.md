# Running one real ticket end to end

Azure DevOps holds the ticket, GitHub holds the code. That split is the normal case and the
thing the product claims, so it is the right shape to test.

Read the two traps first. Both of them fail a run for a reason that has nothing to do with
the change, and both cost several minutes to discover from the other end.

---

## Two traps

### 1. The target repository must be Python with at least one real test

`localhost/kuwarden-python312:1` is the only toolchain image that exists today, and the
default `test_command` is `python -m pytest -q`.

Worse: **`pytest` exits 5 when it collects no tests.** That is non-zero, so the Coder reads it
as a failing suite, burns all four attempts trying to fix code that was never broken, and the
run is rejected. A repository with no tests fails 100% of the time.

Either put one real passing test in the repo, or, while you are only exercising the pipeline:

```yaml
test_command: [sh, -c, "python -m pytest -q; test $? -le 5"]
```

### 2. The trigger is configured in two places and they must agree

This is a genuine wart, not a convention:

| Place | Used for |
|---|---|
| Workbench → application → Ticketing (`app_triggers` table) | Admission at `POST /runs`: refuses if nothing is configured |
| `kuwarden.yaml` → `triggers:` | What the Triage node actually reads — the organisation, project, label and points rule that fetch the ticket |

The node reads `kuwarden.yaml`. If the Workbench row says one project and the YAML says
another, the run starts and then fetches from the YAML's project. Keep them identical. The
duplication should collapse into one source; it has not yet.

---

## Setup

### 1. Stack and schema

```bash
podman compose up -d --wait && uv run python -m engine.db migrate
```

### 2. The sandbox image

```bash
uv run python -m engine.sandbox build && uv run python -m engine.sandbox doctor
```

`doctor` prints what this host actually enforces. A rootless podman on cgroups v1 ignores
memory and CPU limits; that is expected here and is why `require_full_isolation: false`. The
degradation is recorded per run in the audit trail, so it is not hidden — it is just not
fixed.

### 3. Application config

```bash
cp kuwarden.example.yaml kuwarden.yaml
```

Fill in `org`/`repo` (GitHub), `organisation`/`project` (Azure DevOps), and read the
`test_command` comment. Nothing in this file is a credential — that is deliberate, since the
file belongs in the application's own repository.

### 4. Tokens

Create them yourself; paste them only into the Workbench, never into a config file, a shell
history, or a chat window.

| Token | Scope needed |
|---|---|
| Azure DevOps PAT | **Work Items → Read & Write** (the flow comments back on the ticket) |
| GitHub PAT (fine-grained, on that repo) | **Contents: Read and write**, **Pull requests: Read and write** |
| Anthropic API key | — |

### 5. Master key, if you have not generated one

```bash
uv run python -m engine.adapters.secrets keygen >> .env
```

### 6. Start the three processes

```bash
uv run python -m engine.worker
```

```bash
uv run uvicorn engine.api.main:app --reload --port 8080
```

```bash
cd ui && npm run dev
```

The worker now refuses to start without `kuwarden.yaml`. That is intentional: a worker that
starts without config accepts work it cannot perform and fails at the first node, several
minutes and one confusing traceback later.

---

## In the Workbench

1. **Create the first account — from the host, not the browser.**

   ```bash
   uv run python -m engine.api create-user you@example.com admin
   ```

   The sign-in page detects an empty deployment and tells you to run this; it does not offer
   a form. That is deliberate: a web form that creates the first admin means whoever reaches
   a fresh deployment first owns it, and a race for the first request is not an access
   control. The password is prompted, never an argument, so it stays out of shell history.
2. **Register the application.** Provider `github`, your org and repo, and an
   `integration_model` — `gated_merge` is the honest choice for a first test, since KuWarden
   is not deploying anything. It is never defaulted, by design.
3. **Ticketing** → provider `azure_devops`, same organisation, project and label as
   `kuwarden.yaml`.
4. **Credentials** — five slots, because grants are narrow and separately revocable. The same
   GitHub PAT goes into three of them:

   | Slot | Value |
   |---|---|
   | `ticket.read_write` | Azure DevOps PAT |
   | `scm.read` | GitHub PAT |
   | `scm.write_branch` | GitHub PAT |
   | `scm.pull_request` | GitHub PAT |
   | `llm.api_key` | Anthropic key |
   | `ci.read` | GitHub PAT — **only if you enabled `ci:`** in `kuwarden.yaml` |

   Stored AES-256-GCM encrypted, bound to this application and slot. They cannot be read back
   out through any endpoint — only replaced or deleted.

5. **Probe.** Verifies the tokens reach the platform and that the declared
   `integration_model` is achievable. Do this before spending a run.

6. **Start run** with your work item id.

---

## What should happen

| Node | What to look for |
|---|---|
| ① Triage | Fetches the work item from Azure DevOps. Refuses if the label is missing — that refusal is correct behaviour, not a bug |
| ② Planner | First model call. A plan appears on the run |
| ③ → ③ⓑ ⇄ ④ Coder / Push / Build & Test | The repository tree is pulled, a workspace is materialised, the model edits, **the branch is pushed**, **real pytest runs in a container**, then — if `ci:` is configured — **your own pipeline is read back for that commit**. Failures feed the next attempt. Up to 4 attempts |
| ⑤ Verifiers | **Stubs.** They pass unconditionally. Nothing is verified here yet |
| ⑥ Gate | `high` suspends and emails approvers; `low` passes automatically |
| ⑦ Release | Opens the pull request against the branch pushed in the loop |

The branch appears on your remote **during** the loop, one commit per attempt, before anything
has verified it — [ADR 0007](adr/0007-push-before-verification.md) explains why, and what
bounds it. If your repository runs CI on every branch push, expect a pipeline run per attempt.

Watch the audit trail at `/runs/{id}`. It is the record — `flow_events` is append-only and
enforced by a database trigger, not by convention.

---

## What this test does *not* prove

Say these out loud before drawing conclusions from a green run.

- **The verifiers are empty.** "A verifier falsified the change" has never been reached by
  anything except a test. Nothing reviewed the diff.
- **The test verdict is an external anchor only if you configured one.** With a `ci:` section
  in `kuwarden.yaml`, Build & Test reads GitHub Actions back for the pushed commit and that
  becomes the verdict (`CIResult.source == "ci"`). Without one — or with no pipeline, or one
  still running when `wait_s` expires — the sandbox verdict stands, `ci_detail` records why,
  and the approval page shows the caveat above the buttons. Absence is never promoted to a
  pass. **Check `source` on the `build_test_verdict` event before concluding anything from a
  green run.**
- **A rejected run leaves its branch behind.** Compensation does not delete it. That was
  invisible while the push happened after the gate.
- **No policy is pinned.** There is no `policy.yaml` loader, so runs record the literal
  `unpinned:no-policy-loader` — deliberately not a plausible-looking SHA.
- **Resource limits are probably not enforced** on a rootless-podman host. Check `doctor`.

---

## When it breaks

| Symptom | Cause |
|---|---|
| `worker has no kuwarden.yaml loaded` | The worker was started from a directory without one. Set `KUWARDEN_CONFIG` |
| `no credential for scm.read ... none stored ... and none in the environment` | A slot was missed in step 4 |
| `... was encrypted with key X, but KUWARDEN_SECRET_KEY is key Y` | `.env` changed after the credentials were stored. Re-enter them |
| `PAY-123 does not carry the 'kuwarden-auto' label` | Working as designed — admission control |
| Rejected after 4 attempts, tests never passed | Very likely trap 1. Check whether pytest collected anything |
| `the Flow Engine is unreachable` | The worker or Temporal is not running |
