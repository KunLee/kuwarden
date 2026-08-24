# ADR 0008 — Application configuration is operator-owned, not repository-owned

- **Status:** Accepted
- **Date:** 2026-08-22
- **Depends on:** [ADR 0003](0003-role-graph-and-traceability.md), [ADR 0004](0004-delivery-integration-models.md)
- **Constrains:** `engine/config_store.py`, the Workbench API, the worker's startup contract
- **Partially supersedes:** [ADR 0003](0003-role-graph-and-traceability.md) §1, which states that `kuwarden.yaml` "lives in each application repository"

---

## Context

Until now a worker loaded one `kuwarden.yaml` from its own filesystem at startup and handed
the resulting `AppConfig` to every node, whichever application the run belonged to.
Credentials were already resolved per application; configuration was not. Registering a second
application therefore produced runs that read the *first* application's repository list,
tiering rules and merge policy while holding the *second* one's tokens — and nothing anywhere
said so.

The obvious fix was to do what [ADR 0003](0003-role-graph-and-traceability.md) §1 already
described: read each application's `kuwarden.yaml` from its own repository, per run. That
sentence is why the file holds no credentials, and why `**/kuwarden.yaml` is a protected path
an agent may never write ([invariant 10](../../CLAUDE.md)).

Attempting it surfaced the problem. ADR 0003's own rejected alternatives say:

> **Encode permissions in `kuwarden.yaml`, per application** — *Rejected.* It lets an
> application grant itself capabilities by editing a file in its own repository. Privilege
> definition must sit outside the thing being privileged.

The intended reconciliation was that `kuwarden.yaml` "may only *select from* what
`policy.yaml` already permits". **There is no `policy.yaml` loader** — invariant 8 is the one
row in the invariant table enforced by nothing at all. So a repository-owned file today is a
repository-owned file with no cap on what it may assert.

## Decision

**Application configuration is owned by the operator and stored in the Workbench.** It is
resolved per run from the `app_config` table, keyed on the application, and no part of it is
read from the application's repository.

The control point (`integration_model`) is **not** taken from the configuration at all. It
lives in `app_registry`, changeable only through an endpoint that records the change in the
append-only `app_changes` table — [ADR 0004](0004-delivery-integration-models.md). Where a
stored configuration disagrees, the run is **refused** rather than resolved by precedence.

## Why the split we first proposed does not survive

The tempting shape was *governance in the Workbench, mechanics in the repository*: let the
application team own their toolchain, test command and workspace layout, and keep only the
authority-granting settings under operator control.

Every candidate for the "mechanics" half turns out to determine a **verdict**:

| Setting | What a team could do with it |
|---|---|
| `sandbox.test_command` | `[true]` — Build & Test passes forever |
| `workspace.repos` | Point the Coder and Push at a different repository |
| `ci.required_workflows` | Empty it, or name a workflow that always succeeds |

None of these grants a credential. Each decides what *"the tests passed"* means, which is
[invariant 3](../../CLAUDE.md)'s entire subject. For a product whose claim is that verdicts
come from outside the thing being judged, that is the same failure in different clothing — and
it is the failure ADR 0003 named when it said privilege definition must sit outside the thing
being privileged. The split was a weaker restatement of a rule that was already there.

## Consequences

**Invariant 8 stops blocking multi-application deployments.** `policy.yaml` was a prerequisite
only because something had to cap what a repository-supplied file could assert. No
repository-supplied file, no cap needed. `policy.yaml` remains owed for org-level defaults and
for invariant 8 itself; it is no longer on the critical path for serving more than one
application.

**Configuration is no longer versioned with the code it governs.** A run against an older
commit is governed by today's configuration. This is a real loss, and the honest fix is not to
move the file back into the repository: it is to **pin the resolved configuration into the run
record**, the way `policy_commit` already is ([ADR 0003](0003-role-graph-and-traceability.md)
§4). That also closes an existing gap — `flow_runs` does not record which configuration
governed a run, so changing `integration_model` silently re-interprets every past run. Owed,
and not done here.

**The platform team becomes the edit path for every application's settings.** At scale that is
a queue in front of a person, which is the failure risk tiering exists to avoid elsewhere. The
answer is per-application permissions in the Workbench, not a file in someone's repository.

**`**/kuwarden.yaml` stays a protected path.** Belt and braces now rather than load-bearing: a
repository may still contain one, and an agent writing it should still be denied.

**A fallback remains, deliberately.** An application with no stored configuration is governed
by the worker's own startup file, so existing single-application deployments keep working and
can migrate one application at a time. The fallback is safe only because the Triage guard
`assert_configured_for` refuses a run whose application does not match the configuration it
was handed. Without that guard the fallback would silently govern the wrong run — which is the
defect this ADR exists to close.

## Alternatives considered

### Fetch `kuwarden.yaml` from the application repository per run

*Rejected for now.* It is the shape ADR 0003 §1 describes and it has a real advantage:
configuration versioned with the code it governs, reviewed in the team's own pull request. It
is unsafe **until `policy.yaml` exists**, because every setting in the file influences a
verdict and nothing would cap what a team may assert about their own changes.

**Revisit when** invariant 8 is enforced — a `policy.yaml` loader plus a constraint evaluator.
At that point the repository file can return as a *selection* from operator-permitted options,
which is what ADR 0003 always intended it to be.

### Split the schema — governance in the Workbench, mechanics in the repository

*Rejected.* See above; `sandbox.test_command` alone disproves it.

### Shred the YAML into database columns

*Rejected.* Two representations of one schema drift, and applications would behave differently
depending on which path their configuration arrived by. The YAML is stored verbatim and parsed
by the one parser in `engine/config.py`, which keeps a single set of validation rules and one
documented file format.

### One worker process per application

*Not rejected — superseded as the default.* It works, needs no code, and gives strong
isolation. It does not scale past a handful of applications, and it leaves configuration next
to the engine rather than under the governance of the product whose entire claim is
governance. Still the right answer for an application that needs a genuinely isolated worker.
