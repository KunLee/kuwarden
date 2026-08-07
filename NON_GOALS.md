# Non-Goals

What KuWarden deliberately does not do. Every entry here was considered and rejected; several
are things a reasonable person would expect the product to include.

The purpose of this file is to make scope refusals cheap. Without it, every quarter someone
re-proposes the same four features and the team re-derives the same answers.

---

## Product scope

**We do not build a better coding agent.**
The Coder node is a swappable execution substrate — OpenHands, Claude Code, or direct model
calls behind an adapter. Every hour spent on file-editing loops is an hour not spent on the
flow engine, gates, release adapters and audit trail, which is the only part that is ours.
Competitors are well funded and are already very good at the coding agent.

**We do not compete on agent orchestration UI.**
GitHub Agent HQ and UiPath for Coding Agents own "run many agents from one console". Fighting
there means fighting an SCM incumbent and an automation incumbent on their home ground, with
their distribution.

**We do not stop at the pull request.**
The inverse of the above, and the reason the product exists. Everything after the PR — release,
promotion, verification, rollback, evidence — is the differentiator. A feature that improves
pre-PR experience at the cost of post-PR capability is a bad trade.

**We are not a general workflow engine.**
KuWarden automates *software change delivery*. Requests to model arbitrary business processes
are out of scope; Temporal is directly available to anyone who wants that.

**We do not offer a hosted SaaS control plane.**
Self-hosting is not a deployment option, it is the product. A hosted control plane would
dissolve the only thing that distinguishes us for regulated buyers.

---

## Autonomy scope

**We do not aim for zero human involvement.**
Humans move from *doing* to *deciding*. The goal is to make review cheap and to spend scarce
human attention only where risk warrants it — not to remove humans. A design that removes the
human from a `high`-tier change is a bug.

**We do not let an agent judge its own work.**
Verification reads external systems of record. This is not tunable, per-application or
otherwise.

**We do not let an agent hold release credentials**, in any integration model, under any
configuration flag.

**We do not auto-handle every ticket.**
Ambiguous, exploratory, or architecture-shaped tickets are rejected back to a human at intake.
Attempting them produces plausible, expensive, wrong work — the most costly failure mode
available.

---

## Technical scope

**We do not hand-roll durable execution.**
See [ADR 0001](docs/adr/0001-flow-engine-control-plane.md). Exactly-once side effects, durable
timers and workflow versioning are the whole problem, and they are Temporal's problem.

**We do not build a visual flow builder** (currently listed in ROADMAP Phase 6 — that entry is
superseded by this one). Building an editor for arbitrary flows before one flow is proven
inverts the order of evidence. Reconsider only when multiple registered applications have
demonstrably diverging topologies that configuration cannot express.

**We do not pin our identity to a single LLM vendor**, and we do not name models in strategy
documents. Model identifiers live in `docs/reference/models.md` with a review date.

**We do not support unrestricted agent network egress**, even when it would make dependency
resolution easier. See [ADR 0005](docs/adr/0005-sandbox-contract.md).

**We do not fan out contract-coupled changes across agents.**
Consistency has to come from a single authoring context; it cannot be recovered by
reconciliation afterwards. See [ADR 0002](docs/adr/0002-flow-topology.md).

---

## Things that are not non-goals, but are *not yet*

Listed separately so nobody mistakes sequencing for refusal.

| Item | When |
|---|---|
| Azure DevOps, GitLab, Bitbucket DC adapters | After one platform works end to end |
| Multi-repository workspaces | Contract fixed in [ADR 0005](docs/adr/0005-sandbox-contract.md); implementation follows the single-repo case |
| `orchestrator-workers` fan-out | On the revisit triggers in [ADR 0002](docs/adr/0002-flow-topology.md) |
| Custom agent / tool-server SDK | After the internal node contract has survived contact with more than one application |
| Cost attribution and chargeback | Token metering exists from Phase 1; reporting comes later |
