# 2026-08-07 · 01 — Documentation review, market reality check, and Flow Engine design

**Participants:** K'Smart · Claude (Opus 5)
**Starting state:** 5 markdown files, 2 commits, no code.
**Ending state:** 3 ADRs, 5 diagrams, `roles.example.yaml`, rewritten `ARCHITECTURE.md`. Still no code. Nothing committed.

---

## Context

The repository contained VISION, ARCHITECTURE, LLM_STRATEGY, ROADMAP and TOOLS_LANDSCAPE —
well written, internally consistent, and describing a system for a market that had moved.
The session began as a documentation review and turned into an architecture redesign.

---

## What happened

### 1. Documentation review

Findings, roughly in order of consequence:

- **The name is taken.** [kuflow.com](https://kuflow.com) is an existing commercial product —
  a developer-oriented workflow engine with human tasks and approvals, built on Temporal.io,
  listed on the UiPath marketplace. Same name, adjacent category, overlapping vocabulary.
- **Every model reference was ~20 months stale** (Qwen2.5-Coder-32B, Claude 3.5 Sonnet,
  GPT-4o, DeepSeek-Coder-V2). The default coding model was justified with **HumanEval**, a
  saturated benchmark. Structural fix agreed: strategy documents must not name models at all.
- **Missing entirely:** `README.md`, non-goals, evaluation strategy, threat model, cost model,
  ADRs.
- **The sequential pipeline was the wrong abstraction** — one-shot per-file generation with no
  compile, no test feedback, no iteration.
- **No execution sandbox contract**, no context/retrieval strategy, no concurrency story.

### 2. Market reality check

Research established that the white space claimed in `TOOLS_LANDSCAPE.md` — "nobody owns the
orchestration layer above coding agents" — had closed:

| Competitor | Position |
|---|---|
| **GitHub Agent HQ** | Multi-vendor agent "mission control", branch-level access control, sandboxed execution, enterprise policy |
| **UiPath for Coding Agents** (May 2026) | "Orchestrate, deploy, monitor and govern AI coding agents"; their stated thesis — *"the orchestration layer is the constant"* — is nearly verbatim our §6 |
| **Atlassian Agents in Jira** (beta, Mar 2026) | Agents as board assignees; Rovo Dev does plan → generate → check → open PR |
| **OpenHands v1.6.0** (Mar 2026) | K8s enterprise self-hosting, Agent Control Plane, SAML/SSO, RBAC, budgets |

**What remains uncontested**, and became the recommended positioning:

1. True sovereignty **off GitHub** — Azure DevOps / on-prem GitLab / Bitbucket DC.
2. **Everything past the PR** — every competitor stops at the pull request.
3. **Compliance evidence as the product**, not a feature. EU AI Act enforcement opened
   2 Aug 2026; 84% of organisations cannot pass an audit of agent behaviour or access control.

Also noted, and it shaped the gate design: the binding constraint in 2026 is **human review
capacity**, not code generation. A design with two mandatory human gates per run scales into
the problem it exists to solve.

### 3. "Why do we need an orchestrator at all?"

Fair challenge — the proposal was: a clear, approved ticket goes to an agent that codes,
compiles, tests, deploys. The answer that settled it: that design gives one non-deterministic
component three roles that must be separated — **actor, judge, and publisher**.

Six forces made the case: durability across multi-day waits, separation of duties, blast
radius (prompt injection via ticket content reaching production credentials), concurrency,
failure cleanup, and enforceable policy.

Agreed explicitly: **before the PR you barely need a Flow Engine; after the PR you cannot
operate safely without one.** This is recorded in ADR 0001 as a "when this does not apply"
section so nobody cites it to over-build.

### 4. Graph Engineering transcript

A video transcript on "Graph Engineering" was brought in. Assessment: it independently
validated the orchestrator argument nearly point for point, and added four things we adopted —
**adversarial verifiers**, **risk-tiered routing**, **context isolation as an architectural
motivation**, and the **work graph / role graph** vocabulary.

Three things were explicitly *not* adopted:

- The claim that LangGraph's killer feature is *durable execution*. It checkpoints state; a run
  still dies with its process. For flows that wait days and perform irreversible side effects,
  that distinction is the whole problem. → Temporal.
- The 90.2% / 15× multi-agent figure — that is a *research* workload shape, not ours.
- The term "Graph Engineering" itself. Our documents face enterprise architects and auditors;
  "state machine", "workflow", "durable execution" are stable and understood.

### 5. Terminology collision

`orchestrator` was being used for two opposite things: the **control-plane layer** (which must
contain no LLM) and the **`orchestrator-workers` topology** (whose defining feature *is* an
LLM planning at runtime). Resolved by naming the layer the **Flow Engine** throughout, and
always writing `orchestrator-workers` in full.

### 6. Traceability

Added on request. The sharpening that mattered: **a role graph on its own is not
traceability.** It states what may happen; the run tree states what did. Traceability is the
join, and the join is only sound if every run pins the policy version that authorised it.

---

## Decisions

| Decision | Record |
|---|---|
| Flow Engine is a deterministic control plane containing no LLM; Temporal for durable execution | [ADR 0001](../docs/adr/0001-flow-engine-control-plane.md) |
| Flow is a graph: risk router, bounded `Coder ⇄ Build & Test` loop, verifiers in fresh context, risk-tiered gates | [ADR 0002](../docs/adr/0002-flow-topology.md) |
| `orchestrator-workers` rejected for now, with three explicit revisit triggers | [ADR 0002](../docs/adr/0002-flow-topology.md) |
| Version-controlled role graph; every run pins `roles_sha` + `policy_bundle`; deny-wins revocation | [ADR 0003](../docs/adr/0003-role-graph-and-traceability.md) |
| Audit trail is a **tree** (`parent_run_id` / `root_run_id`) from the first migration | [ADR 0002](../docs/adr/0002-flow-topology.md) |

Two schema columns were added purely because they are cheap now and impossible to retrofit —
audit data is append-only by definition, so it cannot be migrated freely: `parent_run_id` and
`roles_sha`.

---

## Corrections

Recorded because they are more instructive than the conclusions.

**Claude — overstated the rule on LLM decomposition.** Said "the LLM must not do task
decomposition", which is wrong as stated: reading a ticket and producing a change plan is
exactly what the Planner does. Caught by K'Smart asking how a deterministic router could
possibly parse natural language. The correct line:

> **The LLM decides what to do. It does not decide what it is allowed to do.**
> The LLM proposes; the role graph disposes.

**Claude — mis-sequenced the sandbox.** Advised "write the sandbox spec before Phase 1", then
listed it as an owed item for later. Caught by K'Smart: the Coder cannot run without it, so
the `Coder ⇄ Build & Test` loop — the source of nearly all code quality — does not exist
without it. It is Phase 1, not later.

**Design defect found in ADR 0002 while answering a question.** Risk tiering was specified as
happening once, at intake. But the facts it depends on — which paths the diff touches, whether
it reaches `migrations/`, diff size — **do not exist until after the Coder has produced a
diff.** Tiering must be two-stage:

| | When | Basis | Authority |
|---|---|---|---|
| Provisional | ① Triage | ticket metadata, declared scope, story points | may be wrong; used for admission and budget |
| Final | after ④, before ⑥ | the actual diff | authoritative; sets gate depth |

"May only be raised, never lowered" applies to both. **Not yet fixed in ADR 0002 or the
topology diagram.**

**Corrected in the source documents:** `ARCHITECTURE.md` §2.2 stated *"each agent's output
becomes the next agent's input context."* Correct for `Planner → Coder`; actively harmful for
`Coder → Verifier`, where it makes the author mark their own work.

---

## Open

| Item | Note |
|---|---|
| **Two-stage risk tiering** | Defect above. Needs ADR 0002 + `flow-topology` diagram updated. |
| **`ADR 0004` — sandbox contract** | MVP-critical. Interface + five security properties agreed verbally, not written. |
| **`THREAT_MODEL.md`** | First entry: prompt injection via ticket content. |
| **`EVALUATION.md`** | Highest priority of the remaining docs — the verifier design cannot be shown to work without it. |
| **`roles.yaml` JSON Schema + constraint evaluator** | Needed in CI from Phase 0, or the constraints are decorative. |
| **Name collision** | Unresolved. Should be settled before the name enters package paths. |
| **Workload identity infrastructure** | ADR 0003 promotes SPIFFE/SPIRE from optional hardening to platform prerequisite. Affects Phase 0 scope — flagged, not confirmed. |
| **Nothing committed** | All work is uncommitted on `main`. |

---

## Artefacts

**Created**

```
docs/adr/README.md
docs/adr/0001-flow-engine-control-plane.md
docs/adr/0002-flow-topology.md
docs/adr/0003-role-graph-and-traceability.md
docs/reference/roles.example.yaml
docs/diagrams/README.md
docs/diagrams/render.mjs
docs/diagrams/{responsibility-split,flow-topology,system-architecture,role-graph,traceability-chain}.{mmd,svg,png}
log/README.md
log/2026-08-07-01-review-and-flow-engine-design.md
```

**Modified**

`ARCHITECTURE.md` — ADR references and the governing rule; §1 rendered diagrams; §2.1 Temporal
and the credential/verification responsibilities; §2.2 rewritten as a node graph with the inner
loop, context-isolation rule and risk tiering; §2.3 MCP layering note; §3 data flow rewritten;
§4 Temporal and sandbox in the deployment topology; §5 prompt injection, credential boundary
and separation of duties added at the top of the security table; **§6 Governance & Traceability
added**.

**Not modified, but flagged in review:** `VISION.md` (comparison table no longer defensible),
`LLM_STRATEGY.md` (model names, HumanEval), `ROADMAP.md` (no evaluation or sandbox work; flow
builder in Phase 6 is premature).
