# Knowledge base

**Reconciled from `log/` on 2026-08-08.** Read this to pick the project up. Read `log/` to
find out how it got here.

---

## What this file is, and what it is not

There are four kinds of document in this repository and they answer different questions. The
most common way to waste time here is reading the wrong one.

| Document | Question | Property |
|---|---|---|
| [CLAUDE.md](../CLAUDE.md) | *What am I not allowed to break?* | Rules. Read before writing code |
| [docs/adr/](adr/) | *Why is it like this?* | Decisions. **Immutable once accepted** |
| [log/](../log/) | *How did it get like this?* | Journal. **Append-only, chronological** |
| **This file** | *What is true right now?* | Synthesis. **Rewritten as things change** |

This file is the only one of the four that is *reconciled*. The log is append-only, which
means an early entry that later turned out to be wrong stays wrong on the page — deliberately,
because the record has to show what was believed at the time. That is right for a journal and
useless for onboarding, so the corrections are resolved here instead.

**It does not restate the ADRs.** An ADR is the authority on its own decision; a summary of
one is a second copy that drifts. What lives here is what no single ADR owns: the state of
things, the cross-cutting lessons, and the operational knowledge.

---

## Superseded by later work — do not act on these

If you read `log/` chronologically you will meet all of these as live statements. They are
not.

| Stated in | Now |
|---|---|
| 01: the project is called **KuFlow** | **KuWarden** since 2026-08-08. `kuflow.com` is an unrelated existing product in an adjacent category |
| 01: `roles.yaml`, `roles_sha` | **`policy.yaml`, `policy_commit`.** Only one of that file's eight sections was actually roles. "Role graph" survives as the name of the graph-shaped part |
| 01: two-stage risk tiering is a defect **not yet fixed** | Fixed in ADR 0002 and the topology diagram |
| 01: sandbox contract is **owed, agreed only verbally** | [ADR 0005](adr/0005-sandbox-contract.md), and implemented — `engine/sandbox` |
| 01, 02: **nothing committed** | Everything through `238bd1a` is on `main` |
| 01: name collision **unresolved** | Resolved before any package path existed, which was the cheapest possible moment |
| 02: MVP defaults *proposed, not confirmed* — integration model A | Model **C** (`gated_deployment`) is the ADR 0004 default and what the Workbench pre-selects nothing for. Model A was never confirmed |

---

## Where things stand

**The control plane works, a run edits real code, and the verdict can come from outside.** A
run goes ticket → real repository tree → sandboxed edit loop, pushing a branch and grading
each attempt against both the sandbox and the project's own GitHub Actions pipeline →
verifiers → gate → pull request → comment, through real Temporal and real PostgreSQL. A run
whose worker is destroyed mid-flight is finished by a different worker. **251 tests**; `ruff`
and `mypy --strict` clean.

| Component | State | Where |
|---|---|---|
| Flow Engine — 9 nodes, gates, compensation, audit tree | Working | `engine/flows`, `engine/nodes` |
| Ticket adapters — Azure DevOps Boards, Jira Cloud | Working | `engine/adapters/ticket` |
| SCM adapters — Azure Repos, GitHub, incl. `read_tree` | Working | `engine/adapters/scm` |
| LLM adapter — Anthropic | Working; 3 providers declared, unimplemented | `engine/adapters/llm` |
| Credential storage — AES-256-GCM | Working | `engine/adapters/secrets.py` |
| Sandbox — podman, capability-probed, runs real `pytest` | Working | `engine/sandbox` |
| Workbench — register, ticketing, credentials, runs, users | Working, authenticated | `engine/api`, `ui/` |
| Authentication — argon2 + signed session, 3 roles | Working | `engine/api/auth.py` |
| Planner node | Has a model | `engine/nodes/planner.py` |
| Coder node — real tree, real edits, real test loop | Working | `engine/nodes/coder.py` |
| Push node — branch written inside the loop, idempotent, never forced | Working | `engine/nodes/push.py` |
| Approval gate — evidence document, digest binding, email | Working | `engine/evidence.py`, `engine/activities/notify.py` |
| Run trigger — `POST /api/applications/{id}/runs` | Working; manual only, no webhook | `engine/api/main.py` |
| Four verifier nodes — adversarial, fresh context enforced | Working | `engine/nodes/verifiers.py` |
| Compensation — deletes the branch it pushed, unless a PR exists | Working | `engine/nodes/compensate.py` |
| CI adapter — read a real pipeline verdict | Working, GitHub Actions only | `engine/adapters/ci` |
| `policy.yaml` loader + constraint evaluator | **Does not exist.** The constraints are decorative | — |

### The three that used to block progress — one still does

1. ~~**There is no CI adapter.**~~ **Resolved 2026-08-10.** GitHub Actions is read back for
   the pushed commit and becomes the verdict, with `CIResult.source == "ci"`. The deviation
   from invariant 3 is now **conditional rather than universal**: it applies to any run whose
   application declares no `ci:` section, whose repository has no pipeline, or whose pipeline
   had not finished when `wait_s` expired. In each of those the sandbox verdict stands,
   `ci_detail` records the reason, and the approval caveat names it. What is still owed:
   **Azure Pipelines**, and anchors for SAST, coverage and health — invariant 3 names four
   systems of record and only one is read.
2. ~~**The branch is pushed by the Release node, after verification.**~~ **Resolved
   2026-08-10** by [ADR 0007](adr/0007-push-before-verification.md). Push is now its own node
   inside the loop, between the Coder and Build & Test, so a branch exists while the outcome
   can still change, and item 1 then made that worth something. What this cost is stated in
   the ADR and is not free: unverified model-written code now reaches the customer's SCM, and
   a repository that runs CI on every branch push will run a pipeline against it before a
   human has seen it. The branch it leaves behind is now cleaned up — see *Not built* for what
   compensation does and deliberately does not do.
3. **The `policy.yaml` constraint language does not exist.** The `assert:` expressions in
   `policy.example.yaml` are illustrative pseudocode — not CEL, not Rego, not anything. It
   needs an ADR before it needs an implementation. Until then a run pins the literal string
   `unpinned:no-policy-loader` as its `policy_commit`, which is deliberately not a
   plausible-looking SHA, and the evidence document raises it as a caveat.

### The approval gate, and why the digest exists

ADR 0003 §6 asks for more than "someone clicked approve". The chain:

1. `engine/evidence.py` assembles a document from `flow_runs` + `flow_events` — the audit
   trail, not the workflow's in-memory state, so an approver sees what a regulator would.
2. Its `caveats` list holds everything weaker than it looks: sandbox-graded tests, degraded
   isolation, no pinned policy. Caveats are **inside** the digest, and the UI renders them
   above the buttons. A caveat the approver did not see is not a caveat.
3. `GET /api/runs/{id}/evidence` returns the document and its SHA-256.
4. `POST /api/runs/{id}/approval` recomputes the digest and returns **409** if it moved.
   Runs keep emitting events while they wait, so a stale page is ordinary, not exotic.
5. Only then is `ApprovalSignal` sent to Temporal, carrying the **authenticated** principal —
   never a value the client supplied.

The email is a notification and never the decision: no ticket content (hostile input), plain
text, Bcc so approvers are not disclosed to each other, and a delivery failure is logged
rather than raised — losing the run because a relay hiccuped would be far worse than a missed
email. Without `KUWARDEN_SMTP_HOST` the gate still works and the link is logged at INFO.

---

## Decisions, and where they live

| Decision | Record |
|---|---|
| Flow Engine is a deterministic control plane containing no LLM; Temporal for durable execution | [ADR 0001](adr/0001-flow-engine-control-plane.md) |
| Flow is a graph: risk router, bounded `Coder → Push ⇄ Build & Test` loop, verifiers in fresh context, risk-tiered gates | [ADR 0002](adr/0002-flow-topology.md), [ADR 0007](adr/0007-push-before-verification.md) |
| Role graph; every run pins `policy_commit` + `policy_bundle`; deny-wins revocation; audit trail is a tree | [ADR 0003](adr/0003-role-graph-and-traceability.md) |
| Three delivery integration models; the control point is the last moment we can refuse; `protected_paths` | [ADR 0004](adr/0004-delivery-integration-models.md) |
| Sandbox contract and five security properties, MVP-critical rather than deferred | [ADR 0005](adr/0005-sandbox-contract.md) |
| Tenant credentials encrypted locally in PostgreSQL, behind a Protocol | [ADR 0006](adr/0006-credential-storage.md) |

Decisions taken during implementation that have **no ADR yet** and probably need one:

| Decision | Why it may need a record |
|---|---|
| Sandbox capabilities are probed, never assumed; degradation is recorded in the audit trail | Cross-cuts ADR 0005 and invariant 11 |
| Dependencies baked into toolchain images; no run-time install | Contradicts ADR 0005 §4's "cold install each run", which is unachievable without the deferred egress proxy |
| The workspace is rebuilt per activity, never shared | Follows from Temporal scheduling, not from any ADR |
| Built-in authentication rather than external OIDC | Expensive to reverse once users exist |
| An approval is bound to a digest of the evidence document, and a stale digest is refused | Cross-cuts ADR 0003 §6; changes what "approved" means in the record |
| `CIResult.source` is required and never defaulted, like `control_mode` | The alternative is a sandbox verdict being read later as a CI verdict — invariant 3 |
| UI is React + Vite + Tailwind | Cheap now, expensive after a UI exists |

---

## Lessons that cost something

Distilled from `log/` **by theme rather than by session**, because the same mistake recurs in
different clothes.

### 1. A security property that fails quietly is worse than one that is absent

Three separate instances, all found by tests rather than by reading:

- `protected_paths` used `lstrip("./")`, which takes a *character set* and not a prefix. It
  ate the leading dot and **unprotected every `.github/` path** — reintroducing the exact
  escalation ADR 0004 exists to close.
- `shutil.rmtree(ignore_errors=True)` on the sandbox workspace. Git writes objects read-only,
  Windows refuses to unlink them, and the flag turned a failed deletion into a silent one:
  **a tree of customer source code survived every run** while property 3 read as satisfied.
- `--memory` on rootless podman is accepted and ignored. The sandbox reported a bound it was
  not applying.

The common shape: the control *appears* present. Nobody audits a green check. Hence the
standing rule — **probe, then report what was actually enforced**, and make the absence loud.

### 2. Claiming a control you do not exert is the cardinal sin

This is invariant 11 (`control_mode` is never inferred), but it generalises well beyond it.
Overstating what KuWarden authorised is manufacturing evidence, and for a product whose value
*is* evidence that is worse than any missing feature.

Applied consequences, in code: the sandbox records `sandbox_isolation: degraded` in the
**audit trail**, not just a log line and a UI banner — a run that executed model-written code
under weakened isolation says so permanently. `control_mode` is `NULL` for events that
represent no external effect, rather than defaulted to something.

### 3. Assess readiness against the plan that exists

Asked whether the project was ready to start, the answer given was "no, three blockers". Two
of the three were the MVP's own first tasks. The plan agreed in session 01 was explicit —
*walking skeleton, empty nodes, then models* — and the assessment was made against a more
complete version of the project that nobody had proposed.

Labelling work as a gate stops people starting on work that was already agreed.

### 4. The LLM decides what to do; it does not decide what it is allowed to do

From session 01, and it survived contact with the implementation. "The LLM must not do task
decomposition" was wrong as stated — reading a ticket and producing a plan is exactly the
Planner's job. The line is about *authority*, not capability: the LLM proposes, the role graph
disposes.

### 5. Verify claims about the environment before repeating them

`rootful` and `rootless` were stated backwards, then repeated back, then corrected only
because someone ran the probe. The machine is **rootless**, which is *why* cgroup limits are
silently ignored; rootful would make them work.

### 6. Checkpointing is not durable execution

Session 01 rejected the claim that a graph library's killer feature is durable execution. It
checkpoints *data*; a run still dies with its process. For flows that wait days and perform
irreversible side effects, that distinction is the entire problem — and it is why Temporal is
the flow layer and an agent framework, if used at all, belongs inside a node.

---

## Positioning — the part that constrains engineering

Recorded because it is load-bearing on design decisions, not just on marketing.

The market for "run coding agents" closed during session 01's research: GitHub Agent HQ,
UiPath for Coding Agents, Atlassian Rovo Dev, OpenHands. **KuWarden does not compete there.**
Three things remain uncontested and are the whole product:

1. **Sovereignty off GitHub** — Azure DevOps, on-prem GitLab, Bitbucket DC, on-prem weights.
2. **Everything past the pull request** — every competitor stops at the PR.
3. **Evidence as the product**, not a feature.

The engineering consequence is concrete: an air-gapped install must work. That is why the
credential store is local-first rather than a cloud secret manager, and why the sandbox has no
egress. A design that quietly requires internet access forfeits the only uncontested ground.

Also load-bearing: **the binding constraint in enterprise AI delivery is human review
capacity, not code generation.** A design with mandatory human gates per run scales into the
problem it exists to solve, which is why gate depth is a function of `risk_tier`.

---

## Operational knowledge

Things that cost time to discover.

### This development machine

- **podman is rootless on cgroups v1.** `--memory`, `--cpus`, `--pids-limit` are accepted and
  **silently ignored**. `uv run python -m engine.sandbox doctor` reports what is actually
  enforced. `ulimit -v` and `tmpfs size=` do work and are used instead.
- Switching to rootful uses **separate storage** — existing containers and volumes disappear
  and need recreating. Do not do it mid-configuration.
- `pkill -f uvicorn` does not work from Git Bash on Windows. Use `Get-NetTCPConnection
  -LocalPort N` → `Stop-Process`.
- The Windows console is cp1252 and cannot encode `✓` `✗` `·`. CLI output uses ASCII markers.
- Vite falls back to 5174 when 5173 is taken; check its output rather than assuming.

### Required configuration, no defaults

Both are deliberate — a credential that works out of the box is a credential nobody replaces.

| Variable | Consequence of losing it |
|---|---|
| `KUWARDEN_POSTGRES_PASSWORD` | `compose up` refuses to start |
| `KUWARDEN_SECRET_KEY` | **Every stored credential must be re-entered.** Back it up separately from the database — a key stored beside the ciphertext it protects is not encryption |

### Anthropic API shapes that changed

Verified against the live reference, not memory. Details in
[docs/reference/models.md](reference/models.md) with a review date.

- `budget_tokens` → **400**. Use `thinking: {"type": "adaptive"}` and `output_config.effort`.
- `temperature` / `top_p` / `top_k` → **400**. There is no sampling knob.
- Assistant-turn prefill → **400**. Use `output_config.format`.
- **A refusal is HTTP 200 with an empty `content` array.** Check `stop_reason` before reading
  content — ticket text is hostile by assumption, so this arrives on precisely the inputs the
  system exists to survive.

---

## Not built — the honest list

Ordered by what it costs to be missing, not by effort. Every row is a thing a demo could
skate past and a customer could not.

| # | Missing | What it means today | Blocks |
|---|---|---|---|
| 1 | **`EVALUATION.md`, a golden task set, and a harness to run it** | The verifiers are real and nothing measures whether they *work*. Every test in the suite uses `MockTransport` or `FakePlatform`, so replacing all four verifier prompts with "always pass" would leave 326 tests green. **Made urgent 2026-08-22:** all three nodes moved from `claude-opus-5` to `claude-sonnet-5` with no instrument capable of detecting a regression. **Agreed as the next substantial piece of work after the demo.** The set is *data* — tickets plus human-curated expected outcomes, weighted toward cases that must be REJECTED, since a verifier that examines nothing is indistinguishable from one that works on happy-path cases alone | Any claim the verifier design works. Every prompt or model change is an invisible regression until it exists |
| 2 | **`policy.yaml` has no loader** | The `assert:` expressions in `policy.example.yaml` are illustrative pseudocode. Runs pin the literal `unpinned:no-policy-loader` | Invariant 8 entirely. Org-level defaults, which is what stops every application repeating "which model, what budget" |
| 3 | **Budgets are recorded, never enforced** | `budget_cents_spent` is incremented and compared against nothing. `cents_per_run` is decorative | Any deployment where an unbounded model bill matters — i.e. every paid one |
| 4 | ~~**No webhook receiver**~~ | **Resolved 2026-08-22.** `POST /api/applications/{id}/hooks/azure_devops` receives `workitem.updated`, fires only on a *transition into* the ready state (a save that left the state alone carries no `System.State`), checks the tag as a cheap filter with Triage still authoritative, and keys the workflow id on work item + revision with `REJECT_DUPLICATE` so a redelivery is a no-op. Authenticated by a shared secret compared with `hmac.compare_digest`; refuses to run when it is unset | — |
| 5 | **SAST, coverage, health are not read** | Invariant 3 names four systems of record; only the CI exit code is anchored | Invariant 3 holding for more than one of its four clauses |
| 6 | **Azure Pipelines has no CI adapter** | GitHub Actions only — and Azure DevOps is the flagship ticket system, so the gap is in an odd place | Invariant 3 on an all-Azure deployment |
| 7 | **Configuration is pinned per run, but not per commit** | **Partly resolved 2026-08-22** ([ADR 0008](adr/0008-configuration-is-operator-owned.md)). Configuration is operator-owned and resolved per application from `app_config`, so one worker serves many; the worker's own file is a fallback for applications with nothing stored, and Triage's `assert_configured_for` refuses a run whose application does not match what it was handed. **Still missing:** `flow_runs` does not record *which* configuration governed a run, so changing a setting silently re-interprets every past run | Reading the configuration that actually applied to a historical run — the same pinning `policy_commit` already has |
| 8 | **An admin who forgets their password cannot recover** | `create_user` and `disable` exist; no change-password endpoint | Recoverable on a laptop via the CLI, not at a customer site |
| 9 | **Only one toolchain image exists** | `python312`. Any other stack needs a Containerfile and a rebuild — the sandbox has no egress, so dependencies must be baked in | Non-Python customers |
| 10 | **No metrics** | Nothing counts runs, durations or failures over time | The evaluation metrics CLAUDE.md asks for — PR merge rate, human minutes per run — have nowhere to come from |
| 11 | **The sandbox capability probe fails open** | `capabilities()` reads `"ALLOCATED" not in out` and ignores the exit code, so a probe that could not run at all — image missing, podman machine down — is indistinguishable from one the memory limit killed. It reports `fully_enforced: True, gaps: []`. Demonstrated 2026-08-22 by pointing the probe at a nonexistent image | The isolation banner disappearing exactly when nothing could be verified, and `require_full_isolation: true` passing in production under isolation nobody confirmed |
| 12 | **The run record ends at the pull request** | Release opens it and the run finishes. Nothing watches for the merge, so the evidence package cannot say whether code review happened, who merged, or whether the change reached the default branch. [ADR 0009](adr/0009-two-approval-levels.md) makes this the gap that decision creates | Evidence covering both approval levels. Also the first path that needs `control_mode: "observed"`, which invariant 11 defines and nothing yet uses |
| 13 | **No per-application concurrency cap** | `KUWARDEN_MAX_CONCURRENT_ACTIVITIES` bounds one worker, which is backpressure for the host. Nothing bounds one application's share, so a burst from one team delays every other team's runs | Fairness across applications. Must be a gate *inside* the run, not a rejection at admission — Azure DevOps fires a transition once, and refusing it loses the trigger |
| 14 | **No workflow versioning** | `engine/flows/delivery.py` is replayed from history, so adding, removing or reordering an `execute_activity` call breaks every run that has already passed that point. Observed 2026-08-22: adding `record_run_status` to the gate left a suspended run failing every workflow task with `[TMPRL1100] Nondeterminism error`, with three approvals buffered behind it and no way through | Deploying workflow changes without draining first. Temporal's answer is `workflow.patched()`; until then the rule is operational, not enforced — and nothing warns you |
| 15 | **A rejection names no verifier unless the verdict events fired** | `_verify` emits one `verifier_verdict` per verifier carrying that verifier's notes, but `verifiers_completed` records only `{"passed": N}` — so if the verdict events are missing, the trail says a change was refused and destroys the reason. Observed 2026-08-23 on run `db056fda`: rejected by `test_evidence` for shipping a carousel with no tests *and* a README paragraph justifying it, with zero `verifier_verdict` rows; the finding was only readable from Temporal's workflow history. Cause was a worker started before the emit existed — but nothing makes the absence visible | Two things: workflow code changes need a worker restart and nothing warns you (gap 14), and `verifiers_completed` should name the failing verifier itself rather than relying on sibling events being present |

**Done, and worth not re-deriving:**

| Was missing | Resolved |
|---|---|
| ~~The four verifiers were stubs~~ | 2026-08-11. Adversarial prompts per angle; `test_evidence` counts assertions removed, skips added and test-versus-source churn *before* any model sees it. A verifier that cannot reach a model **blocks** — failing open would put "verified" on a change nothing verified |
| ~~Fresh context was a promise nothing enforced~~ | `_verifier_brief` constructs the state from an allow-list, so `plan`, `retry_count` and the other verdicts are absent by construction. Invariant 4 moved from **review** to **machine** |
| ~~Compensation received the state as it was at run *start*~~ | `_deliver` raising meant `state = await self._deliver(...)` never assigned, so compensation saw no branch and silently did nothing on exactly the runs it exists for. The flow now publishes the latest state as each node returns |
| ~~Compensation did nothing~~ | 2026-08-11. Deletes the branch it pushed **unless a pull request was opened** — deleting is destroying evidence, and once a human is involved removing the branch takes away the thing they were asked to look at. Never raises; records what it did as a `compensated` event |
| ~~The push happened after verification~~ | [ADR 0007](adr/0007-push-before-verification.md) |
| ~~No CI adapter~~ | GitHub Actions, read-only, anchored to the pushed commit |
| ~~A node failure left no trace in the audit trail~~ | `node_failed` and `run_failed`, with the error type and message |

### Deliberately not doing

**Emailing the ticket's author or assignee when a pull request is waiting.** The ticket
comment is the notification; the board's own subscription is the delivery mechanism. Two
reasons, and the second is decisive: a second "who gets told" source will drift from the
first, and Jira hides `emailAddress` behind privacy settings on most instances — so the
implementation would fail silently exactly where it was needed.

---

## Open questions

Ordered by what they block.

| Question | Blocks | Status |
|---|---|---|
| **`control_mode` deviates from ADR 0004** — migration 001 uses nullable + CHECK, not `NOT NULL` | Nothing today; it is on `main` | Needs an amending ADR or a revert. Applied literally, `NOT NULL` forces a value onto events with no external effect, which is the defaulting invariant 11 forbids |
| **Temporal retention** — namespace TTL is 24h, archival disabled | Any claim about long-term evidence | ADR 0001 says Temporal's history *is* the audit record; ARCHITECTURE.md calls PostgreSQL a "projection". Which is authoritative, and for how long, is unanswered |
| **`policy.yaml` constraint language** | The Policy page, CI enforcement of the role graph | The `assert:` expressions are pseudocode. Needs an ADR |
| **No external anchor for SAST, coverage or health** | Invariant 3 holding for all four systems of record it names | The CI exit code is anchored as of 2026-08-10; the other three are not read at all. A run with no `ci:` section still has no anchor, and says so |
| **A rejected run leaves its branch on the remote** | Nothing today; it is untidy and it is the customer's repository | `compensate` is `return state`. Invisible while the push happened after the gate; not invisible now (ADR 0007) |
| **`kuwarden.yaml` is read from the worker's filesystem, not from the repository it describes** | More than one application per deployment | The file is designed to live in the application's own repo and be reviewed there — that is why it holds no credential and is a protected path. Today `engine/worker.py` loads one at startup, so **one worker serves one application**. It is also declared twice: the Workbench row gates admission at `POST /runs`, the YAML is what Triage reads, and they must agree by hand |
| ~~**The control point could not be changed after registration**~~ | — | **Resolved 2026-08-10.** `PATCH /api/applications/{id}/control-point` moves it and records the move in the append-only `app_changes` table, because `flow_runs` does not store which model governed a run — changing it silently re-interprets every past run. Pinning the effective config per run is the real fix and is owed |
| **An admin who forgets their password has no recovery path** | Any deployment whose only admin is locked out | `create_user` and `disable` exist; there is no change-password endpoint anywhere in the API. Recoverable on a laptop via the bootstrap CLI, not recoverable at a customer site |
| **No webhook receiver** | Runs starting from a ticket transition rather than a person | `POST /api/applications/{id}/runs` is the manual path. A webhook needs the engine reachable from the ticket system, which a laptop and an air-gapped site both fail at differently |
| **Workload identity (SPIFFE/SPIRE)** | ADR 0003 calls it a platform prerequisite | Never scoped. Currently stubbed by name-based identity |
| `THREAT_MODEL.md` | — | Two primary threats identified: prompt injection via ticket content, workflow-definition write escalation. ADR 0006's host-compromise limit belongs here |
| `EVALUATION.md` | Any claim that the verifier design works | Unwritten since session 01, where it was called the highest-value remaining document |

### Invariant 2 is now enforced, and where the control sits matters

`assert_may_hold()` fires **when `CredentialRequest` is constructed**, not inside
`CredentialBroker.resolve`. There are three broker implementations and tests inject more;
three copies of a security control drift, and a test double would bypass a per-broker check
entirely. Every path constructs a request, so that is the choke point.

It keys on `may_call_llm` — the same predicate invariant 1 uses — so "agent node" has one
definition and classifying a new node cannot satisfy one invariant while breaking the other.
Deterministic nodes are unaffected: node ⑦ Release is what legitimately merges or deploys
under integration model A (ADR 0004 §4), and a test asserts it still can.

### The invariant table now states its own enforcement

[CLAUDE.md](../CLAUDE.md) grew an **Enforced by** column, never blank. Four of the twelve are
admissions rather than controls — **2, 4, 3 and 8** — and must not be cited as guarantees in
a document, a demo or a README. The same audit found two claims in CLAUDE.md that this
repository did not support: `ruff`/`mypy` "in CI" and "Gitleaks runs in CI". **There is no
`.github/` directory and no pre-commit hook.** Both lines now say what is true.

That is worth reading as a data point about method rather than as a chore: our own operating
document asserted controls we did not exert, survived several sessions, and was caught by
looking rather than by anything failing. It is the failure mode this product exists to
prevent, occurring in the process that builds it.

### Documents that are stale and actively misleading

| File | Problem |
|---|---|
| `ROADMAP.md` | The whole file predates the ADRs: Redis state machine ([ADR 0001](adr/0001-flow-engine-control-plane.md) rejected it), a `Deployer Agent` running `kubectl` (**breaks invariant 2**), the linear pipeline ADR 0002 replaced |
| `VISION.md` | The competitive comparison table is no longer defensible — see *Positioning* above |
| `LLM_STRATEGY.md` | Names models directly, which [CLAUDE.md](../CLAUDE.md) forbids; those names are ~20 months stale |

---

## Picking this up

```bash
# 1. Infrastructure
cp .env.example .env
python -c "import secrets; print('KUWARDEN_POSTGRES_PASSWORD=' + secrets.token_urlsafe(32))" >> .env
uv run python -m engine.adapters.secrets keygen >> .env
podman compose up -d --wait

# 2. Engine
uv sync && uv run python -m engine.db migrate
uv run pytest                      # 140 tests; sandbox and flow tests need the stack up

# 3. Sandbox
uv run python -m engine.sandbox build
uv run python -m engine.sandbox doctor    # what this host actually enforces

# 4. Workbench
uv run uvicorn engine.api.main:app --reload --port 8080
cd ui && npm install && npm run dev
```

Then read, in order: [CLAUDE.md](../CLAUDE.md) → [ADR 0001](adr/0001-flow-engine-control-plane.md)
and [0002](adr/0002-flow-topology.md) → `engine/flows/delivery.py`. The flow file is the
shortest path to understanding the whole shape, because every node is called from it and the
determinism boundary is visible in its imports.
