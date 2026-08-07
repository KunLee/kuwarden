# ADR 0005 — Execution sandbox contract

- **Status:** Accepted
- **Date:** 2026-08-07
- **Depends on:** [ADR 0001](0001-flow-engine-control-plane.md), [ADR 0002](0002-flow-topology.md), [ADR 0004](0004-delivery-integration-models.md)
- **Constrains:** Coder node, Build & Test node, Helm chart, SCM adapter

---

## Context

The `Coder ⇄ Build & Test` cycle in [ADR 0002](0002-flow-topology.md) is where nearly all code
quality comes from: the agent acts, the build runs, the failure comes back, the agent fixes.
That cycle requires somewhere to actually compile and run tests.

**This makes the sandbox MVP-critical, not a later hardening item.** Without it there is no
inner loop, and without the inner loop the topology degrades to the one-shot generation the
ADR was written to replace. Earlier planning listed it as owed work; that was a sequencing
error.

The sandbox is also the largest hidden cost in a system of this kind. Per-language toolchains,
dependency caching, private artifact registry authentication, egress policy, resource limits
and concurrency isolation are individually unremarkable and collectively months of work. The
way to avoid that cost dominating Phase 1 is to **fix the contract now and implement the
thinnest version behind it** — if the interface is right, the implementation can grow or be
replaced without touching the nodes.

A second requirement arrives from a separate direction. A change spanning several services
must be authored in **one** context, because the sub-changes share a contract and splitting
them produces interface drift. The sandbox therefore holds a **workspace**, not a repository.

---

## Decision

### 1. The contract

```python
class Sandbox(Protocol):
    async def exec(
        self,
        workspace: Workspace,        # one or more repos, each pinned to a SHA
        toolchain_id: str,           # e.g. "jdk21-maven3.9", resolved from kuwarden.yaml
        command: list[str],
        timeout_s: int,
        limits: ResourceLimits,      # cpu, memory, pids, disk
    ) -> ExecResult:
        ...

@dataclass(frozen=True)
class ExecResult:
    exit_code: int                   # ← the reality anchor. Nothing else is.
    stdout: str
    stderr: str
    changed_files: list[FileChange]
    duration_ms: int
    limits_hit: list[str]            # "timeout" | "memory" | "disk" | "egress-denied"
```

`limits_hit` is separate from `exit_code` deliberately: "the tests failed" and "we killed it at
600 seconds" are different facts, and the Coder must be able to tell them apart to decide
whether retrying is even sensible.

### 2. Workspace, not repository

```yaml
# kuwarden.yaml
workspace:
  repos:
    - { name: payments-service, path: services/payments }
    - { name: payments-client,  path: clients/payments }
    - { name: payments-schema,  path: schema }
```

The Coder sees one coherent tree. **Consistency comes from unified authoring; safety comes
from sequenced delivery** — the fan-out happens at release (with ordering constraints), never
at authoring. See [ADR 0002](0002-flow-topology.md).

Each repo is pinned to an explicit SHA at workspace construction, recorded in `FlowState`, and
carried into the audit record.

### 3. Five security properties — from the first commit

These are cheap now and effectively impossible to retrofit, because retrofitting them means
auditing every path that already assumed otherwise.

| # | Property | Why |
|---|---|---|
| 1 | **No credentials in the sandbox. Ever.** | The sandbox runs model-directed code. Anything reachable from it is compromised the first time prompt injection succeeds. Package-registry auth is brokered by an egress proxy, never mounted. |
| 2 | **No egress** except an allowlisted package mirror | Otherwise the sandbox is an exfiltration channel for the source code it was given. |
| 3 | **Ephemeral filesystem**, destroyed on completion | No state carries between runs, so one run cannot poison the next. |
| 4 | **Resource and wall-clock limits**, always | An agent loop will otherwise consume a node. `limits_hit` reports which bound was reached. |
| 5 | **The sandbox produces a diff. It does not push.** | Pushing happens outside, by the Flow Engine, under a separate identity. |

Property 5 is the same rule as everywhere else in this architecture: **the thing that produces
a change is never the thing that sends it onward.**

Property 1 has a corollary worth stating, because it is the one people relax under schedule
pressure: a private artifact registry is reached through a brokered proxy that injects
credentials on egress. The token never exists inside the sandbox filesystem or environment.

Protected paths ([ADR 0004](0004-delivery-integration-models.md)) are enforced on the
resulting diff: a `changed_files` entry matching a protected pattern fails the run rather than
being silently dropped, so the attempt is visible.

### 4. MVP scope vs product scope

The contract is fixed. The implementation is deliberately minimal in Phase 1.

| | Phase 1 (≈1 week) | Product |
|---|---|---|
| Languages | One, a single prebuilt image | Many, layered images |
| Dependency cache | None — cold install each run | Layered cache + registry proxy |
| Isolation | One-shot Kubernetes Job | Resource quotas, concurrency classes |
| Latency | Not a goal | Warm pools |
| Customisation | None | Per-application build images |

All five security properties are present in Phase 1. **The performance work is deferrable; the
isolation work is not.**

### 5. Sandbox failures are not code failures

The Build & Test node must distinguish three outcomes, because conflating them teaches the
Coder the wrong lesson and burns retry budget on unfixable problems:

| Outcome | Meaning | Action |
|---|---|---|
| `exit_code != 0`, no `limits_hit` | The code is wrong | Feed output back to the Coder; consume a retry |
| `limits_hit` non-empty | The change may be pathological, or the limits too tight | Consume a retry, but surface the limit explicitly; repeated hits abort |
| Infrastructure error | The sandbox itself failed | **Do not consume a retry.** Retry the infrastructure, then abort to ⑧ |

---

## Consequences

### What this buys

- The inner loop exists in Phase 1, so the quality argument in
  [ADR 0002](0002-flow-topology.md) is testable rather than asserted.
- Cross-service changes are authored coherently.
- The largest cost centre in the system is bounded by an interface, so it can grow without
  rewriting the nodes.

### What this costs

- The sandbox is now a Phase 1 deliverable, which enlarges Phase 1. This is the honest cost of
  the topology decision, surfaced rather than deferred.
- The egress proxy is additional infrastructure in the Helm chart, and is on the critical path
  for any application with private dependencies — which in an enterprise is most of them.
- Cold dependency installation makes early runs slow. Accepted deliberately: correctness and
  isolation first, latency later.

### What we now owe

- The egress proxy design — allowlist, credential injection, and its own audit record.
- A toolchain image registry and the `toolchain_id` resolution rules in `kuwarden.yaml`.
- Retry-budget semantics wired to the three-outcome table above.
- A `changed_files` check against `protected_paths` in the Build & Test node.

---

## Alternatives considered

### Run builds on the existing CI instead of a KuWarden sandbox

*Rejected for the inner loop, retained for verification.* CI round-trips are minutes and CI is
not designed to be called in a tight loop; an agent iterating four times would take half an
hour. The **inner** loop uses the sandbox for speed; the **authoritative** build and test that
gates the change is still the CI system ([ADR 0001](0001-flow-engine-control-plane.md) reality
anchors). The sandbox tells the agent whether it is close; CI decides whether it is right.

### Give the sandbox registry credentials directly — simpler than a proxy

*Rejected.* It is simpler, and it is exactly the concession that makes prompt injection
profitable. The proxy exists so that a compromised sandbox yields nothing worth stealing.

### One repo per run; coordinate cross-service changes at the flow level

*Rejected.* This is the interface-drift failure: contract-coupled sub-changes authored in
separate contexts diverge, and each passes its own tests. See
[ADR 0002](0002-flow-topology.md).

### Defer the sandbox; have the Coder generate without executing

*Rejected — this was the original plan and it was wrong.* Generation without execution is the
one-shot pipeline. The feedback edge is the mechanism.

---

## References

- [ADR 0001](0001-flow-engine-control-plane.md) · [ADR 0002](0002-flow-topology.md) · [ADR 0004](0004-delivery-integration-models.md)
