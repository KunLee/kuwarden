# ADR 0007 — The branch is pushed before the change is verified

- **Status:** Accepted
- **Date:** 2026-08-10
- **Depends on:** [ADR 0002](0002-flow-topology.md), [ADR 0004](0004-delivery-integration-models.md), [ADR 0005](0005-sandbox-contract.md)
- **Constrains:** Push node, Build & Test node, Release node, SCM adapters, CI adapter (unbuilt)
- **Amends the topology in:** [ADR 0002](0002-flow-topology.md) — Release is split, not replaced

---

## Context

[Invariant 3](../../CLAUDE.md) says a gate verdict reads an external system of record — CI
exit code, SAST report, coverage, health endpoint — and never an agent's claim to have
succeeded. That invariant is currently **in deviation**. Build & Test runs the suite in
KuWarden's own sandbox, so the same system that produced the change also grades it. The
deviation is labelled rather than hidden: `CIResult.source` is required, it rides the
`build_test_verdict` audit event, and it becomes a caveat the approver reads before deciding.

Labelling it was the honest move available at the time. It is not a fix, and the fix has been
blocked on something mundane: **CI cannot run on a branch that does not exist.**

Until now the branch was pushed by ⑦ Release, which runs after the verifiers and after the
approval gate. So the project's own pipeline only ever saw a change KuWarden had already
finished deciding about. A CI adapter written against that topology could read a result, but
never one that could still change the outcome — which is not an anchor, it is a receipt.

Nothing else about invariant 3 is hard. The adapter is a day of work per platform. The
ordering was the whole blocker.

---

## Decision

### 1. Split ⑦ Release into **Push** and **Release**

Push moves inside the bounded cycle, between ③ Coder and ④ Build & Test:

```
③ Coder → Push → ④ Build & Test ─┐
   ↑                              │  exit_code != 0, budget remaining
   └──────────────────────────────┘
```

Release keeps the half that was always the point: **opening the pull request**. A pull request
is a request addressed to a human, and it is still made only after the verifiers have passed
and the gate has been satisfied.

Push is `deterministic`. It holds an SCM branch-write credential and nothing else — no merge,
no CI trigger, no deploy. Those are not on the `ScmAdapter` interface at all.

### 2. `protected_paths` is enforced at Push, before the change leaves the building

Invariant 10 was previously enforced in Build & Test, before execution. With the push ahead of
Build & Test, that placement would let a CI definition reach origin and only then be refused —
and a workflow file that reaches origin is executable *there*, whatever KuWarden decides
afterwards. The deny therefore moves to Push.

Build & Test keeps its check. Both call one function; neither owns a copy of the rule. Copies
of a security control drift, and this drift would be invisible: both would keep passing their
own tests while denying different sets of paths.

### 3. Every attempt's tree is `base + that attempt's edits`

The base commit is pinned once, by the Coder, and carried on `FlowState`. Each push builds its
tree from that pinned commit and parents the new commit on the branch's own tip.

Both halves matter and they are separable:

- **Tree from the pinned base.** A file changed in attempt 1 and left alone in attempt 2 must
  not survive on the branch. If it did, the branch CI runs against would contain something
  absent from the diff Build & Test graded, and the two would disagree about what the change
  even is.
- **Parented on the tip.** The branch reads as a history of attempts rather than one commit
  that silently replaced another.

### 4. Pushes are idempotent, and never forced

Temporal re-runs an activity whose effect landed but whose acknowledgement was lost. The
commit message carries `kuwarden-run-id` and `kuwarden-attempt`, which together name one
intended push and no other; an adapter that finds that message on the branch tip returns
instead of committing again.

A branch that has moved to something this run did not write is **refused**, not overwritten.
Force-pushing over it destroys evidence rather than resolving a conflict.

### 5. The push is recorded, and does not carry a `control_mode`

Each push emits a `branch_pushed` event with the branch, the commit, the base and the attempt
number. It is not an `external_effect`, so it carries no `control_mode`. `authorized` and
`observed` name the three control points in [ADR 0004](0004-delivery-integration-models.md); a
branch push is not one of them, and stretching the word to cover it would cost exactly the
narrowness that makes it worth anything (invariant 11).

---

## Consequences

### What this buys

A CI adapter is now writable. When it exists, `CIResult.source` becomes `"ci"` for real, the
caveat in the evidence document disappears **because the underlying fact changed**, and
invariant 3 moves from *review — currently deviating* to *machine*. That is the correct way for
a caveat to disappear, and the only acceptable one.

The Coder's inner loop also gains a second, independent grader without changing its shape.

### What it costs — stated plainly

**Unverified, model-written code now reaches the customer's SCM.** Previously nothing left the
perimeter until the verifiers and a human had both passed it. This is a real widening and it
should be argued with, not waved through. What bounds it:

| Concern | What holds it |
|---|---|
| Arbitrary code executing in the customer's CI | `protected_paths` is denied *before* the push. CI definitions, deploy manifests and IaC cannot be written at all |
| The change being merged | No merge credential exists anywhere in the system, at any node |
| A human being asked to act on unverified work | No pull request until after the verifiers and the gate |
| Confusion with human branches | Branches are namespaced `kuwarden/<ticket>-<run>` |
| The push being mistaken for approval | Recorded as `branch_pushed`, not as an `external_effect`, and carrying no `control_mode` |

The residue that is *not* mitigated: a repository whose CI triggers on every branch push will
now run a pipeline against agent-written code before any human has seen it. That pipeline may
hold credentials KuWarden does not control and cannot inspect. **This is the real cost of this
ADR**, it is the customer's pipeline and not ours, and it is why the CI adapter work should
land alongside guidance on trigger configuration rather than after it.

### What we now owe

1. **The CI adapter** — GitHub Actions, Azure Pipelines. Without it this ADR has paid its cost
   and collected none of its benefit.
2. **Branch cleanup on abort.** Compensation deletes nothing today, so a rejected run leaves
   its branch behind. That was invisible while the push happened after the gate; it is not now.
3. **Trigger guidance** in the operator documentation, per the residue above.
4. The Azure Repos adapter has a **known gap** against §3: the Pushes API expresses a commit as
   changes relative to the branch tip rather than as a tree, so a second push there is
   `tip + edits`. It is written down in the adapter and in the protocol docstring, and it is
   not closable without carrying the base content of every path the run has touched.

---

## Alternatives considered

### Push to a KuWarden-controlled mirror instead of the customer's repository

CI would run somewhere the customer's pipeline never sees, keeping unverified code out of
their SCM entirely.

**Rejected** because it defeats the purpose. The anchor invariant 3 wants is *the project's
own* pipeline — the one whose results the organisation already trusts and already acts on. A
pipeline we host is our sandbox again, with more infrastructure and the same argument against
it. It also requires mirroring credentials, which is a new privileged capability for a
problem we invented.

**Revisit if** a customer's compliance posture forbids agent-written commits on their origin
outright. At that point a mirror plus a `required_status_checks` bridge is the shape to look
at, and the mirror is worth its cost because the alternative is not running at all.

### Push only the final attempt, after the loop

Fewer commits on the branch, and only code that passed the sandbox reaches origin.

**Rejected** because it reintroduces the original problem one step later: CI would run once,
after the loop had already concluded, so a CI failure could not feed the Coder. The feedback
edge is the mechanism ([ADR 0002](0002-flow-topology.md)) and a grader outside it is a report,
not an anchor.

**Revisit if** per-push CI cost turns out to dominate — several pipeline runs per ticket is a
real bill in some organisations. The mitigation is a config knob for which attempts trigger CI,
not a change to where the push happens.

### Force-push each attempt to keep the branch at one commit

A tidier branch and a simpler adapter.

**Rejected.** The attempts *are* the evidence. A force-push destroys the record of what the
agent tried, which is precisely what this product exists to retain, and it makes the "branch
moved under us" case indistinguishable from normal operation.

**Revisit** never, on the audit argument. If branch tidiness matters to a customer, squash on
merge — a human action, on the far side of the gate.

### Keep the push at Release and have CI grade the pull request instead

No topology change at all.

**Rejected** because a pull request is opened after the gate, so this is the "push only the
final attempt" alternative with an extra step: the human has already approved by the time
anything runs.
