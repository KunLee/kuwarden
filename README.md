# KuWarden

> Governed, auditable change delivery — from ticket to production — for enterprises that
> cannot put their code on someone else's cloud.

**Status: early implementation.** The control plane runs, the sandbox runs, and there is a
Workbench. The agent nodes are mostly still empty. See
[Where things stand](#where-things-stand).

---

## What it does

KuWarden sits between your ticket system and your environments. A ticket becomes a planned,
implemented, independently verified, human-approved, released and *evidenced* change — without
a developer hand-off at every step, and without your source code leaving your network.

```
Jira / Azure DevOps ticket
   → ① triage & risk router      (deterministic — rejects unclear work early)
   → ② planner                   (LLM)
   → ③ coder ⇄ ④ build & test    (LLM, bounded loop — the verdict is the CI exit code)
   → ⑤ verifiers ×4              (LLM, fresh context, adversarial)
   → ⑥ approval gate             (depth set by risk tier — may suspend for days)
   → ⑦ release                   (deterministic — holds the credentials)
   → evidence
```

![Flow topology](docs/diagrams/flow-topology.png)

---

## The idea in one rule

> **The agent guesses. The Flow Engine verifies.
> Whatever must be deterministic, auditable, or privileged does not get to be a model.**

The component that produces a change is never the component that certifies it, and never the
component that releases it. That separation is what makes the audit trail worth anything.

![What is allowed to be a model](docs/diagrams/responsibility-split.png)

---

## Why this and not the alternatives

The market for "run coding agents" is crowded and well funded — GitHub Agent HQ, UiPath for
Coding Agents, Atlassian Rovo Dev, OpenHands. **KuWarden does not compete there.**

Three things remain genuinely underserved, and they are the whole product:

| | |
|---|---|
| **Sovereignty, off GitHub** | Azure DevOps, on-prem GitLab, Bitbucket Data Center. On-prem model weights. Nothing leaves the perimeter. |
| **Everything past the PR** | Agent HQ, Rovo Dev, Devin and OpenHands all stop at the pull request. Release, promotion, rollback and verification are where the risk and the evidence live. |
| **Evidence as the product** | A change in production resolves to a person, a policy version, and what the approver actually saw. 84% of organisations cannot currently pass an audit of agent behaviour. |

See [docs/TOOLS_LANDSCAPE.md](docs/TOOLS_LANDSCAPE.md) and [NON_GOALS.md](NON_GOALS.md).

---

## Documentation map

Read in this order.

| | |
|---|---|
| **[CLAUDE.md](CLAUDE.md)** | **Start here to write code.** Invariants, the determinism boundary, conventions. |
| **[docs/KNOWLEDGE_BASE.md](docs/KNOWLEDGE_BASE.md)** | **Start here to pick the project up.** Current state, lessons, operational knowledge, open questions — reconciled from `log/` |
| [VISION.md](VISION.md) | Problem, positioning, who it is for |
| [NON_GOALS.md](NON_GOALS.md) | What we deliberately do not do, and why |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Components, flow topology, data flow, security, governance |
| [docs/adr/](docs/adr/) | The decisions — and the rejected alternatives with revisit triggers |
| [docs/GLOSSARY.md](docs/GLOSSARY.md) | Fixed vocabulary. Terminology drift has already caused one design error. |
| [LLM_STRATEGY.md](LLM_STRATEGY.md) | Backend selection and data sovereignty |
| [ROADMAP.md](ROADMAP.md) | Phased delivery |
| [log/](log/) | How it actually got built, including what turned out to be wrong |

### Architecture decisions

| # | Decision |
|---|---|
| [0001](docs/adr/0001-flow-engine-control-plane.md) | Flow Engine as a deterministic control plane — and why Temporal, not a hand-rolled state machine |
| [0002](docs/adr/0002-flow-topology.md) | Flow topology — the bounded inner loop, verification in fresh context, risk-tiered gates |
| [0003](docs/adr/0003-role-graph-and-traceability.md) | Role graph and end-to-end traceability — policy pinning, deny-wins revocation |
| [0004](docs/adr/0004-delivery-integration-models.md) | Delivery integration models — where the control point sits when someone else's CI deploys |
| [0005](docs/adr/0005-sandbox-contract.md) | Execution sandbox contract |

---

## Running it locally

Requires a container runtime with Compose support (Podman or Docker).

Set a database password first — there is no default, and `compose up` refuses to start
without one:

```bash
cp .env.example .env && python -c "import secrets; print('KUWARDEN_POSTGRES_PASSWORD=' + secrets.token_urlsafe(32))" >> .env
```

```bash
podman compose up -d --wait
```

Two containers, no application:

| | |
|---|---|
| **Temporal** | `localhost:7233` · Web UI at [localhost:8233](http://localhost:8233) — the dev server, embedded SQLite, no external database needed |
| **PostgreSQL** | `localhost:5432` · database `kuwarden`, user `kuwarden`, password from `.env` |

The engine is **not** a service in [compose.yaml](compose.yaml). It runs on the host under
`uv run`, so editing code does not mean rebuilding an image — and so that everything in that
file is precisely the part a cloud deployment replaces with a managed service. Migrating means
deleting the file, not rewriting it.

Other settings are overridable via `.env` — see [.env.example](.env.example). Ports bind to
`127.0.0.1` only, which is defence in depth rather than the control: no credential in this
repository has a working default.

```bash
podman compose down
```

State survives that. To discard it, `podman compose down -v`.

### The engine

A master key is needed before any credential can be stored — see
[ADR 0006](docs/adr/0006-credential-storage.md). Generate one, append it to `.env`, and
**back it up somewhere other than your database backup**: losing it means every stored
credential has to be re-entered.

```bash
uv run python -m engine.adapters.secrets keygen >> .env
```

```bash
uv sync && uv run python -m engine.db migrate && uv run python -m engine.worker
```

### The sandbox

Where the Coder's inner loop executes — [ADR 0005](docs/adr/0005-sandbox-contract.md). Build
the toolchain image once, then check what this host actually enforces:

```bash
uv run python -m engine.sandbox build && uv run python -m engine.sandbox doctor
```

`doctor` matters more than it looks. Rootless podman on a cgroups v1 host **accepts
`--memory` and silently ignores it**, so the sandbox probes by running a container rather
than by asking `podman info`, and reports what is actually applied. A sandbox that claims a
bound it is not enforcing is the same class of error as an audit row claiming `authorized`
for something merely observed.

With `sandbox.require_full_isolation: true` (the default), a host that cannot enforce cgroup
limits **refuses to run** rather than running while under-reporting. What still holds on such
a host: the wall clock, the egress block, per-process memory via `ulimit -v`, and the disk
quota via `tmpfs size=`. What is lost: total memory across processes, and CPU.

```bash
uv run python -m engine.sandbox smoke
```

Dependencies are baked into the toolchain image. With no egress there is no `pip install` at
run time — that is the design, not a gap to route around.

### The Workbench

```bash
uv run uvicorn engine.api.main:app --reload --port 8080
```

[localhost:8080](http://localhost:8080) — register an application, store its credentials,
probe what the platform can actually do. Credentials are **write-only**: they are encrypted
before reaching PostgreSQL and no endpoint returns one, only whether it exists.

Then run the suite — the walking-skeleton tests need the stack up, and skip themselves when
Temporal is unreachable:

```bash
uv run pytest
```

Workflow histories are visible at [localhost:8233](http://localhost:8233) while runs execute.

---

## Where things stand

**Built and tested.** A run goes ticket → branch → commit → pull request → comment through
real Temporal and real PostgreSQL. A run whose worker is destroyed mid-flight is finished by
a different worker. The audit trail refuses `UPDATE` and `DELETE`. Credentials are encrypted
at rest and write-only through the API. The sandbox has no egress, no credentials, and
reports which resource limits the host actually enforces rather than assuming.

| Working | Where |
|---|---|
| Flow Engine — eight nodes, gates, compensation, audit tree | `engine/flows`, `engine/nodes` |
| Adapters — Azure DevOps, Jira, Azure Repos, GitHub | `engine/adapters` |
| LLM adapter — Anthropic implemented, three more declared | `engine/adapters/llm` |
| Credential storage — AES-256-GCM, [ADR 0006](docs/adr/0006-credential-storage.md) | `engine/adapters/secrets.py` |
| Sandbox — podman, capability-probed | `engine/sandbox` |
| Workbench — register, credentials, runs, audit trail | `engine/api`, `ui/` |

**Not yet written.**

| Item | Note |
|---|---|
| Coder, and the four verifiers | The Planner is the only node with a model. The Coder writes a marker file until the sandbox can materialise a real base tree |
| Authentication | The Workbench API is **unauthenticated**. ADR 0003's approval constraints depend on approvers being identifiable humans |
| Ticketing configuration in the Workbench | The schema supports it; the UI does not yet write it |
| `THREAT_MODEL.md` | Primary threats identified: prompt injection via ticket content, workflow-definition write escalation. ADR 0006's host-compromise limit belongs here |
| `EVALUATION.md` | Blocks any claim that the verifier design works |
| `policy.yaml` schema + constraint evaluator | The `assert:` expressions in [policy.example.yaml](docs/reference/policy.example.yaml) are written in a language that does not exist yet |
| `ROADMAP.md` | Still describes the pre-ADR design and contradicts ADR 0001 and ADR 0002 |

**How it got built**, including what turned out to be wrong, is in [log/](log/).

**Naming.** This project was called *KuFlow* until 2026-08-08. It was renamed because
[kuflow.com](https://kuflow.com) is an unrelated existing product in an adjacent category — a
Temporal-based workflow engine with human tasks. The rename happened before any package paths
existed, which was the cheapest moment it could have.

*Warden*: one who guards, and one who keeps the records. Both halves of the product.

---

## Licence

[MIT](LICENSE)
