# 2026-08-30 — A red deployment that was not a failure, and the replay that caused three runs

Previous: [2026-08-29-10](2026-08-29-10-caching-preconditions.md).

---

## Context

The first real runs since the caching work landed. Ticket 50 — *redesign the right corner user
profile feature* — produced a merged pull request, a green production deployment, and two red
rows on the Vercel dashboard. The red rows were read as a serious escape: a build error that
reached deployment without the pipeline catching it.

It was not that. But finding out what it *was* uncovered something worse, one layer up.

---

## What happened

### The red previews were the retry loop, seen from outside

Vercel builds a preview for every branch push. [ADR 0007](../docs/adr/0007-push-before-verification.md)
has KuWarden push **before** verification, deliberately, because CI cannot anchor a commit it
cannot see. The consequence nobody had stated: every failed attempt is a public artifact, and
anything watching the repository reacts to it.

Seven deployments, seven agreements:

| commit | Vercel | KuWarden |
|---|---|---|
| `e1dbbf1` attempt 0 | Error | sandbox exit 2 |
| `c99fddf` attempt 1 | Ready | exit 0, CI-anchored |
| `263a97e` **merge of PR #22 → main** | **Ready, Production** | — |
| `0c02101` | Ready | exit 0, CI-anchored |
| `871e860` attempt 0 | Error | sandbox exit 2 |
| `7355386` attempt 1 | Ready | exit 0, CI-anchored |

The timing is the clearest part of the record. `871e860` pushed at 13:08:15; Vercel started
building it at 13:08:26 and failed; KuWarden's own sandbox reached the same verdict at
13:08:35, twenty seconds later. The Coder fixed it, pushed `7355386` at 13:12:17, and both
graders passed it.

**A red preview on an attempt-0 commit is the loop working, not an escape.** Nothing broken
reached production, and `next build` never once disagreed with the CI anchor.

### The real finding: one ticket, three runs, replayed out of order

Ticket 50 produced three complete pipeline runs. The `workflow_id` says where they came from
and, more usefully, in what order:

| run | started | revision | outcome |
|---|---|---|---|
| `521065f3` | 12:46:57 | **r6** | merged, deployed |
| `35e21e71` | 13:03:06 | **r2** | suspended at the gate |
| `d49100db` | 13:05:26 | **r4** | suspended at the gate |

Revision 6 arrived first, then 2, then 4. Azure DevOps replayed a backlog — the behaviour when
a subscription is recreated, which [log 09](2026-08-24-09-demo-hardening-and-context-assembly.md)
records happening twice in one day because a quick tunnel mints a new hostname on every
restart.

The hook's idempotency was already careful, and careful about the wrong axis:

> A service hook delivered twice means it once, so the hook derives an id from the work item
> revision.

That makes a **repeated** revision a no-op. It has no notion of a revision being **older** than
one already run. So each replayed revision started a whole pipeline against an intent that r6
had already superseded — three runs branching from the same base, unaware of each other, two of
them heading for a pull request against a state of the ticket that no longer exists.

**The idempotency key stopped duplicates and could not see disorder.**

---

## Decisions

- **A monotonic revision guard on the hook path.** A delivery whose revision is not greater
  than the highest already launched for that work item is refused, and the reason names what
  superseded it. Revisions are monotonic, so an older one's content is already contained in a
  newer one; running it can only produce a stale pull request.
- **The manual button is untouched**, and deliberately. `_launch`'s docstring already draws the
  line — *a human pressing the button twice means it twice* — and a human cannot create a
  replayed backlog by hand. Re-running a superseded revision on purpose stays possible; it just
  requires saying so.
- **The guard is not a lock, and says so.** The `flow_runs` row is written by an activity once
  the workflow is running, so two deliveries close enough together can both pass it. It closes
  the replay case, where deliveries are minutes apart. The same-revision case stays Temporal's,
  which is airtight and needs no help.
- **One function owns the hook's workflow-id format**, because the guard reads the revision back
  out of it. A format written twice is two formats the day someone edits one.

---

## Corrections

Two of mine, both stated as fact during the investigation and both disproven by the deployment
list a few minutes later.

**"Vercel built the merged main, so the merge result is broken."** It is not. `263a97e` — the
merge of PR #22 — built green and deployed to production. What the line numbers actually proved
was narrower: Vercel was not building `c99fddf`. That should have stopped at *some other
branch*, followed by asking which, instead of naming one.

**"The CI anchor is weaker than what actually deploys."** Unsupported. Every commit KuWarden
passed, Vercel also passed under `next build`. The concern is worth keeping as a question — the
record still cannot answer it — but it was presented as a finding on no evidence.

The shape of both errors is the same: a real observation (the line numbers do not fit) extended
into a conclusion the observation did not carry. The deployment list was one screenshot away the
whole time.

---

## Open

- **Two suspended runs pinned to a stale base.** `35e21e71` (r2) and `d49100db` (r4) sit at the
  gate against `64aa9636`, while main has moved to `263a97e`. They are the guard's own backlog,
  created before it existed, and want terminating rather than approving.
- **`ci_detail` cannot be audited.** It says `passed: CI` and nothing else — not which commit
  the pipeline run was matched to, not what the workflow ran, not how long the wait was. The
  adapter checks the commit and discards runs for any other; a reader of the record cannot see
  that it did. Compare the sandbox section on the same page, which lists command, image, exit
  code, duration, files materialised and every isolation gap, under a heading admitting it is
  *not an independent witness*. The half that carries the invariant is the thinner half.
- **Build & Test does not name which half of a compound command failed.** `test_command` is
  `npm run lint && npm run typecheck`; exit 2 cannot distinguish them, and the reader has to
  expand 1,584 characters of stdout to learn it was `tsc`, six errors, all in one file.
- **The Coder giving up is not a first-class fact.** Run `521065f3` exhausted four inner
  attempts and shipped code it knew was failing. That is in the summary text and nowhere
  queryable — from outside it looks like Build & Test found the problem, when the Coder already
  knew.
- **The sandbox ran degraded.** cgroups unavailable, so container-total memory, CPU quota and
  the process-count cap were not enforced — per-process rlimit only. Recorded and displayed,
  which is right, and still a partial gap against invariant 12 on this host.

---

## The measurement ADR 0011 was waiting for

It arrived on its own, in run `521065f3`:

```
Coder, outer attempt 1   input 1,433   cache written 19,194   cache read 57,582
Coder, outer attempt 2   input   492   cache written 18,656   cache read 18,656
run_cost                 72.79 cents
```

**The Coder's sequential attempts read the cache.** 57,582 cache-read tokens against 1,433
charged at the ordinary rate, in a node whose first attempt wrote 19,194. That is the one
question [ADR 0011](../docs/adr/0011-tool-based-retrieval.md) still had open after log 10 closed
the verifier half by reasoning — and the answer is yes.

The status flip to Accepted is not taken here. It is one-way, and it belongs to whoever is
deciding to build the tool loop rather than to the session that happened to observe the number.

---

## Artefacts

**New** — `log/2026-08-30-11-replayed-webhook-revisions.md`

**Changed** — `engine/api/main.py` (`_hook_workflow_id`, `_revision_of`, `_superseded_by`, and
the guard in the Azure DevOps hook), `tests/test_workbench_api.py`.

Suite: **387 passed, 1 skipped** (podman-gated), against a live database. `ruff` and
`mypy --strict` clean.
