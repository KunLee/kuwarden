# ADR 0003 — Role graph and end-to-end traceability

- **Status:** Accepted
- **Date:** 2026-08-07
- **Depends on:** [ADR 0001](0001-flow-engine-control-plane.md), [ADR 0002](0002-flow-topology.md)
- **Constrains:** `policy.yaml`, PostgreSQL schema, Tool Bus, Monitoring UI

---

## Context

[ADR 0002](0002-flow-topology.md) introduced the distinction between the **work graph** (what
one run actually did) and the **role graph** (which identities exist and what they may do). It
asserted that permissions are never model-decided, but did not specify the role graph itself.

This record specifies it, and specifies the thing that makes it useful.

**A role graph on its own is not traceability.** It states what *may* happen. The run tree
states what *did* happen. Traceability is the **join** between them, and the join is only
sound if every run records which version of the policy authorised it. Without that, a policy
edit silently rewrites the meaning of every historical record — the audit trail still shows
what happened, but no longer shows what was permitted at the time.

The requirement is not theoretical. 2026 governance frameworks state it directly: Singapore's
IMDA framework (January 2026) requires each agent to carry a verifiable identity **and** an
audit trail of which agent acted under whose authorisation; the CSA Agentic Trust Framework
(February 2026) applies Zero Trust to non-human identities; the EU AI Act enforcement window
opened 2 August 2026. Meanwhile 84% of organisations cannot pass a compliance audit of agent
behaviour or access control, and only 23% have any agent identity strategy at all.

For KuWarden this is not a compliance checkbox. Evidence is the product — it is the part of the
value proposition that GitHub Agent HQ, Rovo Dev, Devin and OpenHands do not deliver, because
they stop at the pull request and never own the deployment that an auditor actually asks
about.

---

## Decision

**KuWarden maintains a version-controlled role graph, and every flow run pins the exact policy
version that authorised it. Traceability is the join between the two, and it must resolve in
both directions.**

### 1. The role graph — `policy.yaml`

A single, version-controlled, change-reviewed file describing five things:

| Section | Answers |
|---|---|
| `identities` | **Who exists** — every human role and every non-human (agent, node, service) identity |
| `capabilities` | **What can be done** — verbs on resources, declared once |
| `roles` | **Bundles** of capabilities, with explicit `deny` entries for what must never be held |
| `bindings` | **Who holds what**, scoped to applications and environments |
| `approval_authority` | **Who may approve** which risk tier, and the minimum number of approvers |

`policy.yaml` describes the **platform deployment**, not an application. It is distinct from
`kuwarden.yaml`, which lives in each application repository and describes that application's
flow. An application cannot grant itself capabilities: `kuwarden.yaml` may only *select from*
what `policy.yaml` already permits.

A worked example is at
[docs/reference/policy.example.yaml](../reference/policy.example.yaml).

### 2. Non-human identity is a workload identity, not a name

An agent node's identity is not the string `"coder"`. It is a verifiable workload identity —
a SPIFFE SVID, or a Kubernetes ServiceAccount federated to the enterprise IdP — that the
target system can independently authenticate.

This matters because the audit question is not "which prompt ran" but "which principal
authenticated to the SCM and pushed this commit". A name in a log is an assertion; a workload
identity is a fact the receiving system verified.

### 3. Invariants — the role graph is machine-checkable

The role graph carries its own `constraints` block, evaluated in CI on every change to
`policy.yaml`. A violation fails the build.

```yaml
constraints:
  - id: no-llm-holds-deploy
    description: No identity that runs an LLM may hold any deploy capability.
    assert: |
      identities.where(kind == 'agent-node')
                .effective_capabilities()
                .none(matches('deploy.*'))

  - id: prod-requires-two-humans
    assert: approval_authority.where(tier == 'high').min_approvers >= 2

  - id: no-agent-self-approval
    description: An agent identity may never satisfy an approval requirement.
    assert: approval_authority.all(principals.kind == 'human')
```

This is the same instinct as the rest of the architecture: **policy that matters is enforced
deterministically, not asserted in prose.** A governance rule that only exists in a document
is a rule that will be violated without anyone noticing.

The first constraint is the machine-checkable form of the rule from
[ADR 0001](0001-flow-engine-control-plane.md) — *whatever must be privileged does not get to
be a model*. If someone later grants the Coder deploy access, CI fails.

### 4. Policy pinning — the join

**Every flow run pins `policy_commit` at start**, alongside the model versions it will use.

```sql
ALTER TABLE flow_runs
  ADD COLUMN policy_commit    TEXT NOT NULL,   -- git SHA of policy.yaml at run start
  ADD COLUMN policy_bundle JSONB NOT NULL; -- resolved effective policy, denormalised
```

`policy_commit` alone would require the git history to remain available forever to interpret an
audit record. `policy_bundle` stores the *resolved* effective permissions for that run, so the
record is self-describing. Both are kept: the SHA for provenance, the bundle for
interpretation.

Child runs inherit the parent's `policy_commit`. A run's policy does not change under it.

### 5. Revocation — deny wins

Pinning must not become a way to keep using a permission that has been withdrawn. Therefore
every privileged action is checked against **both** the pinned policy and the current policy,
and **the more restrictive answer wins**:

| Pinned says | Current says | Result |
|---|---|---|
| allow | allow | allow |
| allow | deny | **deny** — revoked mid-run; run halts and reports |
| deny | allow | **deny** — was not authorised when the run was admitted |

This keeps historical interpretation stable while making revocation effective immediately,
including for runs already suspended at an approval gate.

### 6. The delegation chain

Every privileged action records the full chain from a human to the effect:

```
human requester            OIDC subject from the enterprise IdP
  → ticket                 system-of-record ID (JIRA PAY-1234)
  → flow run               root_run_id / parent_run_id  ·  policy_commit  ·  model versions
  → node execution         workload identity (SVID), node class, context digest
  → tool call              Tool Bus record: tool, arguments digest, result digest
  → target effect          commit SHA · PR number · deploy revision · namespace
  ⟂ approvals              who, when, risk tier, and the evidence bundle they were shown
```

`approvals` records the **evidence the approver actually saw**, not merely that they clicked
approve. An approval detached from what was on screen is not evidence of review.

### 7. Both directions must resolve

Traceability that only runs forward is half a system:

| Direction | Question | Asked by |
|---|---|---|
| **Forward** | This ticket — what did it ultimately change, in which systems, approved by whom? | Audit, compliance |
| **Backward** | This running revision in production — where did it come from, who authorised it, under which policy, what evidence existed? | Incident response, security |

Backward resolution is the harder one and drives a concrete requirement: **deploy artefacts
must carry the run identity**. Every commit trailer, image label, and deployment annotation
records `kuwarden-run-id` and `kuwarden-policy-commit`. Without this, backward lookup depends on
correlating timestamps, which fails exactly when it is needed most.

---

## Consequences

### What this buys

- A change in production resolves to a person, a policy version, and an evidence bundle —
  which is the question every regulated customer asks in the first meeting.
- Governance rules become CI-enforced invariants rather than documentation.
- Revocation is immediate without destabilising historical records.
- The role graph is the artefact handed to auditors: small, readable, diffable, and reviewed
  like code.

### What this costs

- Workload identity infrastructure (SPIFFE/SPIRE, or IdP-federated ServiceAccounts) is now a
  platform prerequisite, not an optional hardening step. For an air-gapped install this is
  real deployment surface.
- `policy.yaml` becomes a change-controlled artefact with its own review process. Editing it
  is a governance event, and it must not become a bottleneck for routine onboarding — hence
  `bindings` are scoped so that adding an application does not require a policy change.
- `policy_bundle` duplicates state deliberately. Denormalisation is correct here — audit
  records must not depend on another system still being reachable — but it must be written
  once, at run start, and never updated.

### What we now owe

- A `policy.yaml` JSON Schema and the constraint evaluator, in CI from Phase 0.
- Commit trailers, image labels, and deployment annotations carrying run identity — this
  touches the SCM and deploy adapters and must be designed in, not added later.
- A Monitoring UI view that renders the delegation chain for a single change. The audit trail
  is only evidence if a non-engineer can read it.

---

## Alternatives considered

### Rely on the SCM's and CI's own audit logs

*Rejected.* They record that a bot pushed a commit and a pipeline ran. They cannot answer
which policy version permitted it, what the approver was shown, or which agent identity acted
under whose delegation. They are also per-system, so a change spanning three services produces
three disconnected logs — the exact case the run tree exists to unify.

### Encode permissions in `kuwarden.yaml`, per application

*Rejected.* It lets an application grant itself capabilities by editing a file in its own
repository. Privilege definition must sit outside the thing being privileged.

### Let the Planner decide which capabilities a run needs

*Rejected outright.* This is the failure mode [ADR 0001](0001-flow-engine-control-plane.md)
exists to prevent, and it is directly reachable by prompt injection through ticket content.

### Store only `policy_commit`, resolve against git when auditing

*Rejected.* It makes an audit record uninterpretable if the repository is unavailable,
rewritten, or the run predates a history rewrite. Audit records must be self-describing.

---

## References

- [ADR 0001 — Flow Engine as a deterministic control plane](0001-flow-engine-control-plane.md)
- [ADR 0002 — Flow topology](0002-flow-topology.md)
- [docs/reference/policy.example.yaml](../reference/policy.example.yaml)
- [docs/diagrams](../diagrams) — `role-graph`, `traceability-chain`
- IMDA Singapore, *Model AI Governance Framework for Agentic AI*, January 2026
- Cloud Security Alliance, *Agentic Trust Framework*, February 2026
- SPIFFE / SPIRE — workload identity
