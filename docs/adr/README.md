# Architecture Decision Records

This directory records the significant architectural decisions made on KuWarden — what was
decided, why, what was rejected, and under what conditions a decision should be revisited.

An ADR is written when a decision is **expensive to reverse** or **likely to be questioned
again later**. Routine implementation choices do not get an ADR.

## Format

Each record uses the same sections:

| Section | Purpose |
|---|---|
| **Status** | `Proposed` / `Accepted` / `Superseded by NNNN` / `Deprecated` |
| **Context** | The forces at play. What makes this decision necessary. |
| **Decision** | What we are doing. Stated in the active voice. |
| **Consequences** | What becomes easier, what becomes harder, what we now owe. |
| **Alternatives considered** | What was rejected, and *why* — with revisit triggers. |

Recording **rejected alternatives with revisit triggers** is the highest-value part of an
ADR. The same questions get asked again every six months; the ADR is what stops the team
from re-deriving the answer from scratch.

## Index

| # | Title | Status | Revisit |
|---|---|---|---|
| [0001](0001-flow-engine-control-plane.md) | Flow Engine as a deterministic control plane | Accepted | Not expected |
| [0002](0002-flow-topology.md) | Flow topology — nodes, edges, state, policy | Accepted | On triggers listed in the record |
| [0003](0003-role-graph-and-traceability.md) | Role graph and end-to-end traceability | Accepted | Not expected |
| [0004](0004-delivery-integration-models.md) | Delivery integration models and the control point | Accepted | Per-platform, as adapters are added |
| [0005](0005-sandbox-contract.md) | Execution sandbox contract | Accepted | Not expected — implementation grows behind it |
| [0006](0006-credential-storage.md) | Credential storage — encrypted locally, behind a Protocol | Accepted | When a customer runs Vault or a cloud secret manager, or their threat model includes host compromise |
| [0007](0007-push-before-verification.md) | The branch is pushed before the change is verified | Accepted | If per-push CI cost dominates, or a customer forbids agent commits on their origin |

## Conventions

- Filenames: `NNNN-kebab-case-title.md`, numbered sequentially, never renumbered.
- ADRs are **immutable once accepted**. To change a decision, write a new ADR and set the
  old one to `Superseded by NNNN`. Do not edit history — the point of the record is that it
  shows what was believed at the time.
- Link ADRs from the documents they constrain, not the other way around.
