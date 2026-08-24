# ADR 0009 — Two approval levels: business authorisation and code review

- **Status:** Accepted
- **Date:** 2026-08-22
- **Depends on:** [ADR 0002](0002-flow-topology.md), [ADR 0003](0003-role-graph-and-traceability.md), [ADR 0004](0004-delivery-integration-models.md)
- **Constrains:** the approval gate, `delivery.auto_merge`, the Release node

---

## Context

`gated_merge` gives KuWarden merge authority ([ADR 0004](0004-delivery-integration-models.md)),
and auto-merge now exercises it. That raised a question worth settling: if the approval gate
already collected two approvals in the Workbench, why does a human still merge the pull
request afterwards? It looks like the same decision taken twice.

It is not. They are different questions, asked of different people, against different
artefacts.

## Decision

**A change may pass through two approval levels, and they are not interchangeable.**

| | KuWarden gate | GitHub pull request |
|---|---|---|
| **Question** | *Should this change happen?* | *Is this code correct?* |
| **Artifact** | evidence document — ticket, tier, verdicts, CI verdict, caveats | the diff, line by line |
| **Reviewer** | tech lead, release manager, change owner | a peer engineer |
| **Recorded as** | append-only `flow_events`, bound to an evidence digest | a GitHub review |

The first is **business authorisation**: given what this change is and what the machine found
out about it, should it ship? The second is **code review**: is the implementation right? A
change board authorising a change and an engineer reading the diff are the same arrangement
regulated organisations already run, and collapsing them loses one of the two.

**`delivery.auto_merge.max_risk_tier` is what selects between one level and two.** At or below
the ceiling, the gate is the only human step and KuWarden merges. Above it, KuWarden opens the
pull request and stops, and the code-level review is required. The setting is therefore read
as *"tiers above this also require code review"*, not as an arbitrary ceiling.

## Consequences

**The run record currently ends at the pull request.** Release opens it and the run finishes;
nothing watches for the merge. So the evidence package records the business authorisation and
then goes silent — it cannot say whether code review happened, who did it, or whether the
change reached the default branch. For a product whose stated differentiator is everything
*after* the pull request, that is the gap this decision creates and does not close.

The fix is to record the merge as `control_mode: "observed"` — KuWarden did not authorise it,
it watched it happen. That is precisely the distinction [invariant 11](../../CLAUDE.md) draws,
and this is the first path that needs the `observed` half of it. **Owed, not built.**

**The code-level gate is a convention until branch protection enforces it.** Nothing stops
someone merging without review. ADR 0004 names model B's control point as *branch protection /
required status check*, so `main` should require an approving review and the CI check.
Otherwise "two levels" describes what is hoped for rather than what holds.

**The ordering is business-first, code-second, which is the reverse of most change control.**
The gate runs before Release opens the pull request ([ADR 0007](0007-push-before-verification.md)
pushed the branch early so CI could run at all), so two people authorise a change that a
reviewer may still reject or alter. The evidence digest limits the damage — an approval is
bound to specific facts and goes stale if the change moves — but this is a known asymmetry
rather than a designed one. Revisit if approvers report authorising work that then changed.

## Alternatives considered

### Collapse to one gate — auto-merge at any tier once the approval gate is satisfied

*Rejected.* The argument for it is real and was made first: the Workbench approval is the
**stronger** record, because it binds to what the approver was shown, while a GitHub merge
records only who clicked. Requiring the click afterwards can be read as saying the Workbench
approval was insufficient.

It loses the code review. The two gates read different artefacts — an evidence summary and a
diff — and are answered by different people. Merging automatically after business
authorisation means nobody read the code, which for a change large or sensitive enough to
reach `high` is the wrong trade.

**Revisit when** the same person is doing both. Two levels performed by one human is ceremony,
not control, and at that point the second gate should be removed rather than tolerated.

### Skip the KuWarden gate for tiers that require code review

*Rejected.* It would leave the business decision unrecorded, which is the evidence the product
exists to produce. A GitHub review answers "is this code correct" and says nothing about
whether the change was authorised, by whom, or against what facts.
