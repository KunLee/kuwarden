# ADR 0001 — Flow Engine as a deterministic control plane

- **Status:** Accepted
- **Date:** 2026-08-07
- **Supersedes:** —
- **Constrains:** [ARCHITECTURE.md](../../ARCHITECTURE.md), [ROADMAP.md](../../ROADMAP.md)

---

## Context

The obvious design for KuWarden is the one most teams reach for first:

> A Jira ticket with a clear, approved requirement is handed to a coding agent, which writes
> the code, compiles it, tests it, and deploys it.

This is a legitimate design, and for a narrow set of conditions it is the *correct* design —
see [When this decision does not apply](#when-this-decision-does-not-apply). It is also the
design that fails in a specific and predictable way as soon as those conditions are exceeded.

The failure is not that the agent is not smart enough. It is that the design gives a single
non-deterministic component three roles that must not be held by the same component:

1. the **actor** that produces the change,
2. the **judge** that decides the change is correct,
3. the **publisher** that holds the credentials to release it.

Published 2026 figures make the consequence concrete: leading autonomous agents report
~85% failure on complex tasks, ~14.3% of AI-generated code snippets contain security
vulnerabilities (vs 9.1% human-written), and unreviewed AI code carries ~23% higher bug
density. A component with that error profile must not be the one that certifies its own
output and then deploys it.

Six forces make a separate control plane necessary:

**Durability.** A flow spans a multi-day human approval wait, a 10–40 minute CI run, and a
deployment health check. No process can be held open across that. State must be persisted
and execution must be resumable, without re-running LLM calls that have already been paid
for and without re-executing side effects that have already happened.

**Separation of duties.** The verdict on "did the tests pass" must come from the CI system's
exit code, not from an agent's assertion that they passed. This is not engineering
fastidiousness; it is an audit requirement, and the regulatory window for it is open — the
EU AI Act enforcement period opened 2 August 2026, and Singapore's IMDA agentic framework
(January 2026) requires an audit trail of which agent acted under whose authorisation.

**Blast radius.** If the agent deploys, the agent must hold production deployment
credentials. The agent's input is a ticket, and anyone who can file a ticket can write text
that the agent reads as instructions. Prompt injection via ticket content then becomes a
path to production. The credential boundary must sit outside the LLM.

**Concurrency.** Multiple flows touch the same repository: branches drift, the base moves,
two agents edit the same file, two deploys target the same namespace, N agents exhaust the
inference quota. Something must serialise, lock, and rate-limit.

**Failure cleanup.** A crashed process cannot clean up after itself — the branch is orphaned,
the ticket is stuck in progress, a partial deployment is unrolled back. Compensation must be
driven from outside the thing that crashed.

**Policy.** "Only tickets ≤5 story points", "production needs two approvers", "$20 token
budget per run", "max 3 concurrent runs per repo" are organisational rules. They must be
*enforced*, not requested in a prompt.

---

## Decision

**KuWarden is built around a Flow Engine: a deterministic control plane that owns flow state,
verification, gates, credentials, and policy. The Flow Engine contains no LLM.**

The dividing line is stated as a single rule:

> **The agent guesses. The Flow Engine verifies.
> Whatever must be deterministic, auditable, or privileged does not get to be a model.**

### Responsibility split

| | Agent (node) | Flow Engine (control plane) |
|---|---|---|
| Nature | LLM — non-deterministic | State machine — deterministic |
| Allowed to fail | Yes, routinely | No |
| Privileges | Minimum: read code, write to a feature branch | Maximum: CI, merge, deploy credentials |
| Decides success? | **Never** | Yes — by reading objective results from external systems |
| Audit value | A transcript. Not evidence. | Append-only state transitions. Is evidence. |
| Change rate | Per run | Slow, versioned, reviewed |

### Reality anchors

Every gate decision must be anchored to a machine-verifiable fact, never to a model's
opinion. KuWarden is fortunate here: unlike research or customer-support agents, software
delivery ships with world-class objective anchors already built.

| Gate | Anchor of record |
|---|---|
| Does it build? | Compiler / build exit code |
| Do the tests pass? | CI system exit code — not the agent's claim |
| Is it covered? | Coverage tool output vs configured threshold |
| Is it safe? | SAST findings (Semgrep / Bandit / ESLint) |
| Did it deploy? | Pod readiness + service health endpoint |
| Did it hold? | Error rate over a soak window |

An LLM may *summarise* an anchor for a human. It may never *substitute* for one.

### Credential boundary

| Principal | May hold |
|---|---|
| Agent node | Read-only repo access; write access to its own feature branch; nothing else |
| Flow Engine | CI trigger, PR merge, deploy credentials, ticket transitions |

Deployment credentials are acquired by the Flow Engine after gates pass, and are never
present in any process that has an LLM in it.

### Technology: Temporal

The Flow Engine is implemented on [Temporal](https://temporal.io) rather than a hand-rolled
state machine over PostgreSQL and Redis.

Rationale:

- **Durable execution, not just checkpointing.** The distinction matters here and is easy to
  get wrong. Checkpointing a graph's state preserves *data*; it does not preserve
  *execution*. A run that lives in a single process dies with that process. KuWarden's flows
  wait days for humans and minutes-to-hours for external systems, so surviving process death
  is a base requirement, not an optimisation.
- **Side-effect idempotency on replay.** This is the sharpest edge. On recovery, a naive
  replay re-opens the pull request, re-comments on the ticket, and re-triggers the
  deployment. Temporal's activity model is built precisely around not doing that.
- **Multi-day timers and signals.** Human approval gates are a first-class primitive rather
  than something bolted on.
- **Execution history as audit trail.** Temporal's history is an append-only, replayable
  record of every state transition. This is a direct input to KuWarden's compliance evidence
  story rather than a second system to build and reconcile.

**Note on framework marketing.** Several agent frameworks describe state checkpointing as
"durable execution". For most applications the difference is academic. For KuWarden — given
multi-day waits and irreversible side effects — it is not. Where an agent framework is used,
it is used *inside a node* (see [ADR 0002](0002-flow-topology.md)), never as the flow layer.

### Naming

The control plane is called the **Flow Engine** throughout the codebase and documentation.

The word *orchestrator* is avoided as a standalone noun: it is currently overloaded across
UiPath, Temporal, Kubernetes, and the `orchestrator-workers` multi-agent topology, and the
last of those means very nearly the opposite of what is decided here (an *LLM* that plans at
runtime). Where the multi-agent pattern is meant, it is always written in full as
`orchestrator-workers`.

---

## Consequences

### What this buys

- A flow survives engine restarts, pod eviction, and KuWarden's own deployments.
- Human approval can take two days without holding any resource open.
- A hallucinated "all tests passed" cannot reach production.
- Prompt injection via ticket content cannot escalate to a production deployment.
- The audit trail is a by-product of execution rather than a feature to be built.

### What this costs

- Temporal is an additional operational dependency — a server, a datastore, and worker
  fleet management. For an air-gapped enterprise install this is real deployment surface,
  and the Helm chart must cover it.
- Flow logic must be written to Temporal's determinism constraints (no wall-clock reads, no
  unseeded randomness, no direct I/O in workflow code). This is a genuine learning curve for
  contributors and must be documented before external agents are accepted.
- Two execution models exist in the system — deterministic workflow code and
  non-deterministic node code. The boundary must be obvious in the directory layout, or it
  will be violated.

### What we now owe

- A `THREAT_MODEL.md` whose first entry is prompt injection via ticket content.
- An `EVALUATION.md` — a deterministic control plane makes agent quality *measurable*, but
  does not make it *good*.
- A documented determinism convention for contributors.
- Deployment credentials modelled as a Temporal-side capability, never as node config.

---

## When this decision does not apply

Stated explicitly so the record is honest, and so nobody cites this ADR to over-build:

If **all** of the following hold, a script plus a coding agent is the correct design, and
this ADR should not be invoked:

- one application, one team;
- fewer than ~5 tickets per week;
- non-production environments only;
- a single approver who is present synchronously;
- manual rollback is acceptable and nobody will ask who authorised what.

This describes the Phase 1 vertical slice, which stops at the pull request. The Flow Engine
earns its cost at the point where changes are **deployed**, run **concurrently**, or must be
**audited** — which is precisely the segment KuWarden targets, and precisely the segment that
existing agent tooling does not serve.

**Rule of thumb: before the PR you barely need a Flow Engine; after the PR you cannot
operate safely without one.**

---

## Alternatives considered

### Hand-rolled state machine on PostgreSQL + Redis

*Rejected.* This is re-implementing durable execution, which is a well-known trap. The parts
that look easy (persist a step, resume from it) are easy; the parts that sink the project are
exactly-once side effects, replay safety, timer durability, and versioning a running
workflow's code. Originally implied by [ARCHITECTURE.md](../../ARCHITECTURE.md) §2.1 and now
superseded.

### Agent framework as the flow layer

*Rejected for the flow layer, retained inside nodes.* State checkpointing does not survive
process death, and side-effect replay safety is not part of the model. Appropriate for the
inner loop of a single node; not for a flow that waits days and deploys to production.

### Let the agent deploy, with a strong prompt telling it to be careful

*Rejected.* Policy asserted in a prompt is a request, not a control. It cannot be audited,
cannot be enforced, and fails open under prompt injection.

### No control plane; a CI pipeline calls the agent as a step

*Partially accepted — for Phase 1 only.* CI is an acceptable host for the ticket-to-PR slice
and is the recommended starting point. It is not sufficient once flows require multi-day
async approval, cross-run concurrency control, or compensation on failure: CI systems are
built to run to completion, not to suspend for two days and resume with intact state.

---

## References

- [ADR 0002 — Flow topology](0002-flow-topology.md)
- Anthropic, *Building Effective Agents* — workflows vs. agents; start simple
- Temporal — durable execution, child workflows, signals
- EU AI Act (Regulation (EU) 2024/1689), enforcement from 2 August 2026
- IMDA Singapore, *Model AI Governance Framework for Agentic AI*, January 2026
- Cloud Security Alliance, *Agentic Trust Framework*, February 2026
