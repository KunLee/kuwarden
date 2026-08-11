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
because deploying is only what happens under integration model A. Release opens the pull
request; it does not push the branch — see **push**.

**push**
Node ③ⓑ, inside the inner loop. Writes the branch so that CI has something to run on
([ADR 0007](adr/0007-push-before-verification.md)). Distinct from **release**: pushing moves
code, releasing moves a control point. A push is not an approval and carries no `control_mode`.

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

It receives exactly four things — a directory, an image name, a command, resource limits. No
configuration, no credentials, no network, and no knowledge of which repository or ticket it
is serving. That ignorance is the security property. The model does **not** run here: it runs
in the Coder node, in the worker process. See *What crosses into the sandbox* in
[ARCHITECTURE.md](../ARCHITECTURE.md) §2.2.

**inner loop**
The bounded `Coder → Push ⇄ Build & Test` cycle. Nearly all code quality comes from here. Loops are
not abolished — they are *contained* inside a node, where context is bounded and retries are
budgeted.

**credential broker**
The one thing that turns "I need a token for *this*" into an actual secret. One method,
`resolve(CredentialRequest) -> Secret`, where a request is a **kind** (what the credential is
for — `scm.read`, `ticket.read_write`, `deploy`) and a **realm** (which platform instance —
`github.com:acme`).

It exists so privileged credentials are resolved **at the point of use** and nowhere else.
A credential placed on `FlowState` would be serialised into Temporal's workflow history, and
that history is the audit record — which is append-only, so a token that reaches it has
escaped permanently. Implementations: `EnvCredentialBroker` (development),
`EncryptedPostgresStore` (per application, ADR 0006), `StoreThenEnvBroker` (store first,
environment as fallback).

Not "secret store" — the store is *where* a secret sits; the broker is *who decides* whether
this caller gets it for this realm right now. Invariant 2 is a statement about the broker.

**credential kind**
What a credential is *for*, not what it *is*. Grants stay narrow and separately revocable:
one PAT may be stored under `scm.read`, `scm.write_branch` and `scm.pull_request` as three
entries, so revoking the ability to open pull requests does not revoke the ability to read.

**realm**
The platform instance a credential is scoped to — `github.com:acme`, `jira:PAY`. Keeps one
tenant's token from being resolvable for another tenant's resources inside a single process.

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
