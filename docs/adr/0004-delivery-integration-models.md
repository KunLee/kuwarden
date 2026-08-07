# ADR 0004 — Delivery integration models and the control point

- **Status:** Accepted
- **Date:** 2026-08-07
- **Depends on:** [ADR 0001](0001-flow-engine-control-plane.md), [ADR 0002](0002-flow-topology.md), [ADR 0003](0003-role-graph-and-traceability.md)
- **Constrains:** `kuwarden.yaml`, `policy.yaml`, SCM adapter, deploy adapter, PostgreSQL schema

---

## Context

[ADR 0001](0001-flow-engine-control-plane.md) placed deployment credentials in the Flow
Engine and the approval gate before the deploy step. That design silently assumed **KuWarden
performs the deployment**.

For most enterprises it does not. The repository already has a pipeline. On GitHub, a push or
merge fires Actions; on Azure DevOps, Pipelines; on GitLab, CI. The deployment then runs on a
platform-hosted runner using that platform's own secrets and OIDC federation. Three
consequences follow, and all three break the design as originally written:

1. **The gate is in the wrong place.** If merge triggers deploy, the merge *is* the deployment
   decision. A gate placed after the merge has nothing left to stop.
2. **The credential claim is false.** The Flow Engine does not hold the credentials that
   actually perform the deployment; the CI platform does.
3. **A race exists.** If KuWarden merges and *then* deploys, the platform's own pipeline is
   already deploying the same commit. Double deploy, or two deployments racing.

### The escalation path this exposes

There is a fourth consequence, more serious than the other three.

The Coder holds write access to its own feature branch. **`.github/workflows/` is inside that
branch.** Workflow definitions are executable on push or pull request. An agent that can write
code can therefore write a workflow — which is a direct path from "agent produces a diff" to
"arbitrary code executes with CI credentials", bypassing every gate in
[ADR 0002](0002-flow-topology.md).

This is reachable by prompt injection through ticket content, and it does not depend on which
integration model is chosen. It is fixed first, and unconditionally.

---

## Decision

### 1. Protected paths — unconditional, model-independent

Agent identities are **denied write access** to any path that defines how code is built,
deployed, or governed. Enforced at the SCM tool boundary, not requested in a prompt.

```yaml
# policy.yaml
protected_paths:
  - ".github/workflows/**"
  - ".github/actions/**"
  - ".gitlab-ci.yml"
  - "azure-pipelines.yml"
  - "Jenkinsfile"
  - "charts/**"          # deploy manifests
  - "terraform/**"
  - "**/*.tfvars"
  - "**/kuwarden.yaml"     # else an app can rewrite its own flow config
  - "policy.yaml"
```

This is a **deny**, not a tier escalation. A ticket that genuinely requires a CI change is
routed to a human. If an operator explicitly enables agent authorship for these paths on a
given application, that change is forced to `high` tier and **may never be auto-merged**.

The corresponding CI-enforced invariant is
`no-agent-writes-cicd-definitions` ([ADR 0003](0003-role-graph-and-traceability.md) §3).

### 2. The control point is not "deploy" — it is the last point KuWarden can say no

Restated as the general principle, since it is what the three models are instances of:

> **Place the gate at the last point where KuWarden can still refuse. Where that point is
> depends on who performs the deployment.**

Three integration models. Each application declares one in `kuwarden.yaml`:

```yaml
delivery:
  integration_model: gated_deployment   # kuwarden_deploys | gated_merge | gated_deployment
```

| | **A. `kuwarden_deploys`** | **B. `gated_merge`** | **C. `gated_deployment`** |
|---|---|---|---|
| Who deploys | KuWarden | The existing CI/CD | The existing CI/CD |
| KuWarden's control point | The deploy action | Branch protection / required status check | Platform-native deployment protection rule |
| Holds deploy credentials | **Yes** | No — holds **merge** authority | No |
| Invasiveness | **High** — the existing CD must be disabled or restricted | Low | Low |
| Audit strength | Strongest | Medium — merge authorised, deploy observed | Strong |

**Model C is the default** wherever the platform provides the primitive, because it is
simultaneously the least invasive and nearly the strongest. All three major platforms have it:

| Platform | Primitive |
|---|---|
| GitHub | Environments + deployment protection rules (a GitHub App approves or rejects) |
| Azure DevOps | Environment *Approvals and checks*, including *Invoke REST API* |
| GitLab | Protected environments + deployment approvals |

Under Model C the customer's pipeline runs unchanged, reaches the deployment step, **pauses,
and asks KuWarden**. KuWarden holds no deployment credential and modifies no pipeline, yet is
still the gate. The SCM adapter must therefore **probe for this capability** at application
registration and refuse to select Model C where it is absent.

**Model A additionally requires** that merge does not itself trigger deployment — the target
repository's pipeline must be restricted to tag or manual dispatch. This is why Model A is
high-invasiveness, and it is checked at registration rather than discovered at the first
double deploy.

### 3. Authorised vs observed — the honesty rule

> **The audit trail must distinguish what KuWarden *authorised* from what KuWarden merely
> *observed*.**

Under Model B, KuWarden authorises the merge. What the pipeline then deployed is learned from a
webhook. Recording that as "authorised" would be **manufacturing evidence** — and for a
product whose value proposition is compliance evidence, that is a more severe failure than any
missing feature. An auditor will respect a system that marks the boundary of its own
knowledge; one that is caught overstating it has no evidence value left at all.

```sql
ALTER TABLE flow_events
  ADD COLUMN control_mode TEXT NOT NULL
    CHECK (control_mode IN ('authorized', 'observed'));
```

`control_mode` is surfaced in the Monitoring UI and in every exported report. It is never
inferred and never defaulted.

### 4. Node ⑦ is generalised

[ADR 0002](0002-flow-topology.md) node ⑦ was *"Deploy — sole holder of deployment
credentials"*. That is true only under Model A. It becomes:

> **⑦ Release — the control point, whose mechanism is set by `integration_model`.**

| Model | What node ⑦ actually does |
|---|---|
| A | Merges, then deploys using broker-held credentials, then polls health |
| B | Sets the required status check that permits merge; then observes the downstream deployment |
| C | Responds to the platform's deployment protection callback; then observes the outcome |

In all three the **Coder never holds any of these permissions**, and the reality anchors of
[ADR 0001](0001-flow-engine-control-plane.md) still apply — under B and C they are read from
the platform's reported deployment status rather than executed directly.

---

## Consequences

### What this buys

- KuWarden is adoptable without dismantling an existing delivery pipeline, which is the single
  largest barrier to entry in a large enterprise with many applications.
- The workflow-definition escalation path is closed unconditionally.
- The evidence KuWarden produces is honest about its own limits, which is what makes the rest of
  it credible.

### What this costs

- Three delivery paths to build, test and support instead of one. Model C in particular is
  **per-platform**, so "supports GitHub" and "supports Azure DevOps" are separate pieces of
  work with separate failure modes.
- Model B's evidence is genuinely weaker, and prospects will ask about the gap. The answer is
  to recommend Model C and be direct about B's limits, not to blur them.
- Registration becomes a capability negotiation rather than a config write — the adapter must
  probe the platform and reject unsupportable combinations up front.

### What we now owe

- `THREAT_MODEL.md`, listing workflow-definition write escalation alongside prompt injection
  via ticket content as the two primary threats.
- SCM adapter capability probing, and a registration-time validation that the declared
  `integration_model` is actually achievable.
- A `policy.yaml` constraint and schema entry for `protected_paths`.
- Monitoring UI rendering of `control_mode`, prominently rather than as a footnote.

---

## Alternatives considered

### Only support Model A — KuWarden owns delivery end to end

*Rejected as the sole model, retained as one option.* It is the cleanest architecture and the
strongest audit story, and for a greenfield application it is the right choice. As the only
option it makes adoption a migration project for every application onboarded, which is fatal
in exactly the enterprise segment KuWarden targets.

### Detect the platform and choose the model automatically

*Rejected.* Which control point governs a deployment is a governance decision, not an
inference. It must be declared, reviewed, and visible in `kuwarden.yaml`. The adapter may
*validate* the declaration; it may not make it.

### Record downstream deployments as authorised, since KuWarden authorised the merge

*Rejected outright.* See the honesty rule above.

### Prompt the agent not to modify workflow files

*Rejected.* A prompt is a request. The path is reachable by prompt injection, which means the
control must sit outside anything an attacker can talk to.

---

## References

- [ADR 0001](0001-flow-engine-control-plane.md) · [ADR 0002](0002-flow-topology.md) · [ADR 0003](0003-role-graph-and-traceability.md)
- GitHub — Environments and deployment protection rules
- Azure DevOps — Environment approvals and checks
- GitLab — Protected environments and deployment approvals
