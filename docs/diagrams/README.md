# Diagrams

Two representations are kept for each diagram, deliberately:

| Format | Purpose | Source of truth |
|---|---|---|
| `.mmd` (Mermaid) | Renders inline on GitHub and in most wikis. Cheap to edit, diffs cleanly in review. | For the *structure* |
| `.svg` → `.png` | Presentation and print. Hand-laid-out, typeset, brand-consistent. | For the *published artefact* |

Mermaid is used where a diagram must stay honest through many small edits. Hand-authored SVG
is used where a diagram will be projected, printed, or put in front of a customer — Mermaid's
automatic layout cannot be trusted at that level of polish.

**When a diagram changes, update both.** A `.mmd` that disagrees with its `.svg` is worse
than having only one.

## Contents

| Diagram | What it argues | Governed by |
|---|---|---|
| `responsibility-split` | What is allowed to be a model, and what is not. The credential and separation-of-duties boundary. | [ADR 0001](../adr/0001-flow-engine-control-plane.md) |
| `flow-topology` | The eight nodes, the bounded inner loop, verification in fresh context, risk-tiered gates. | [ADR 0002](../adr/0002-flow-topology.md) |
| `system-architecture` | The full system, with the enterprise perimeter drawn explicitly. | [ARCHITECTURE.md](../../ARCHITECTURE.md) |
| `role-graph` | Which identities exist, what they may do, and the CI-enforced barrier between any LLM and deployment. | [ADR 0003](../adr/0003-role-graph-and-traceability.md) |
| `traceability-chain` | How one change in production resolves back to a person, a policy version, and the evidence an approver saw. | [ADR 0003](../adr/0003-role-graph-and-traceability.md) |

All are landscape and intended for slides, except `flow-topology`, which is portrait — that is
the honest shape of an eight-stage pipeline, and it works better as a printed page or an
appendix slide than as a squeezed 16:9.

**Which diagram for which audience:**

| Audience | Lead with |
|---|---|
| Executive / buyer | `system-architecture` — the perimeter is the pitch |
| Engineering | `flow-topology`, then `responsibility-split` |
| Security / platform | `role-graph` |
| Audit / compliance / regulator | `traceability-chain` — this is the one that closes the deal |

## Regenerating the PNGs

The SVGs are vector and scale without limit; the PNGs exist only for tools that cannot
consume SVG.

```bash
npm i sharp
node render.mjs        # all diagrams @2x  (e.g. 3200×2240)
node render.mjs 3      # @3x, for print or large-format
```

Checked-in PNGs are `@2x`.

## Conventions

Colour carries meaning. It is not decoration, and it is consistent across all three
diagrams:

| | Meaning |
|---|---|
| **Indigo** `#4F46E5` | Contains an LLM. Non-deterministic. May fail. |
| **Emerald** `#059669` | Deterministic. No LLM. Holds privileges. |
| **Amber** `#D97706` | ★ Reality anchor — a machine-verifiable fact, not a model's opinion. |
| **Pink** `#DB2777` | Human decision point. |
| **Red** `#DC2626` | Compensation, or a hard isolation boundary. |
| **Slate** | External systems and adapters. |

Two rules follow from this and should survive any redesign:

1. **Indigo never touches a credential.** If a future diagram shows an LLM node holding
   deploy access, either the diagram is wrong or the architecture is.
2. **Every gate must be adjacent to an amber anchor.** A gate whose input is only indigo is a
   model marking its own work.
