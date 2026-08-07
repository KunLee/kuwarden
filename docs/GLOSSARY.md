# Glossary

Fixed vocabulary for this project. **Use these words and no synonyms** — in code, comments,
commit messages, and documents.

This is not pedantry. Terminology drift in this project has already produced one real design
error: `orchestrator` was used for both the deterministic control plane and the
`orchestrator-workers` multi-agent topology, which mean nearly opposite things. That
ambiguity survived several documents before anyone noticed.

---

## Core

**Flow Engine**
The deterministic control plane. Owns flow state, verification, gates, credentials and policy.
**Contains no LLM.** Never called "the orchestrator".

**node**
A unit of work in a flow, with the uniform contract `(FlowState) -> FlowState`. Some nodes
contain an LLM; most do not. Not "agent" (only some nodes are agents) and not "step" (a step
is a position in a sequence; a node is a thing).

**node class**
One of `deterministic` (may not call an LLM), `advisory` (LLM output is a suggestion only),
`generative` (LLM authors something), `verifier` (LLM, in a fresh context).

**flow run**
One execution of a flow for one ticket. Identified by `run_id`; may have a `parent_run_id`.

**work graph**
What one run actually did. Emergent, per-run, recorded in execution history.

**role graph**
Which identities exist and what they may do. Slow-changing, version-controlled,
change-reviewed, in `policy.yaml`. This is the artefact handed to auditors. The two graphs have
deliberately different change controls.

---

## Verification and gating

**reality anchor**
A machine-verifiable fact used as a gate input: compiler exit code, CI result, coverage
number, SAST finding, pod readiness, error rate. Never a model's opinion. An LLM may
*summarise* an anchor; it may never *substitute* for one.

**verifier**
A node whose job is to **attempt to falsify** the change, not to assess it neutrally. Runs in
a fresh context that has never seen the Coder's reasoning. A change ships when it survives,
not when it is liked.

**fresh context**
A context constructed for a verifier containing only the ticket and acceptance criteria, the
final diff, and objective evidence. Explicitly excludes the author's reasoning, self-
assessment, and prior attempts.

**risk tier**
`low` | `medium` | `high`. Determines verification depth and how many humans must approve.
May only ever be **raised**, never lowered.

**provisional tier / final tier**
Tiering happens twice. *Provisional* is assigned at intake from ticket metadata, for admission
control and budget allocation — it may be wrong, because the diff does not exist yet. *Final*
is computed from the actual diff and is authoritative for gate depth.

**gate**
A point where the run suspends pending a decision. Depth is set by the final risk tier. A gate
holds no resources open while suspended.

---

## Delivery

**release**
The control point — the last moment KuWarden can refuse. Node ⑦. Prefer this over "deploy",
because deploying is only what happens under integration model A.

**integration model**
`kuwarden_deploys` (A) | `gated_merge` (B) | `gated_deployment` (C). Declared per application in
`kuwarden.yaml`. Determines where the control point sits and who holds deployment credentials.

**control_mode**
`authorized` — KuWarden gated this action. `observed` — KuWarden watched it happen but did not
gate it. Never inferred, never defaulted. Recording `observed` as `authorized` is
manufacturing evidence.

**protected paths**
Paths no agent identity may write: CI definitions, deploy manifests, IaC, `kuwarden.yaml`,
`policy.yaml`. A hard **deny**, not a tier escalation.

---

## Execution

**workspace**
What the Coder operates on: one *or more* repositories, each pinned to a SHA, presented as one
coherent tree. Not "repository" — contract-coupled changes must be authored together.

**sandbox**
The ephemeral, network-restricted, credential-free environment where build and test run. It
produces a diff; it never pushes.

**inner loop**
The bounded `Coder ⇄ Build & Test` cycle. Nearly all code quality comes from here. Loops are
not abolished — they are *contained* inside a node, where context is bounded and retries are
budgeted.

**policy pinning**
Recording `policy_commit` and `policy_bundle` on a run at start, so an audit record remains
interpretable after the policy changes.

**delegation chain**
human requester → ticket → flow run → node execution → tool call → target effect, with
approvals attached to the run. Must resolve **forward** (audit) and **backward** (incident
response).

---

## Terms to avoid

| Avoid | Use | Why |
|---|---|---|
| orchestrator | **Flow Engine** | Overloaded across UiPath, Temporal, Kubernetes; and `orchestrator-workers` means the opposite thing |
| agent (for any node) | **node**, or **agent node** | Most nodes contain no LLM |
| deploy (for node ⑦) | **release** | Only model A actually deploys |
| Graph Engineering | **state machine**, **workflow** | A 2026 coinage; our readers are enterprise architects and auditors, for whom the older terms are stable and understood |
| durable execution (of checkpointing) | **checkpointing** | Checkpointing preserves data; durable execution preserves execution. The difference is the entire reason for [ADR 0001](adr/0001-flow-engine-control-plane.md) |
