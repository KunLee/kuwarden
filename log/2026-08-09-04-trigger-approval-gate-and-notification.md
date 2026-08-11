# 2026-08-09 — the run trigger, the approval gate, and what the gate is allowed to claim

Previous: [2026-08-08-03](2026-08-08-03-first-code-sandbox-and-workbench.md).

---

## The question that started it

> 我现在可以设置 repo 和 devops ticket 了么?

The answer turned out to be **half yes**, and the half that was missing was not obvious from
the outside. Twenty-two Workbench endpoints existed — register an application, store
credentials, declare a Jira or Azure DevOps trigger, probe the platform — and every one of
them worked. But a search for `start_workflow` across `engine/` returned nothing. It appeared
only in tests.

So the platform could be configured completely and could not be made to do anything. The
walking skeleton had always been started by pytest, and nobody had noticed that this was the
*only* way to start it, because every test starts it.

Worth remembering as a review habit: **"the tests pass" and "a person can run it" are
different claims**, and a test suite that constructs its own entry point will never tell you
the product has none.

---

## What was built

### 1. `POST /api/applications/{app_id}/runs`

Approver role, not admin — starting work is an operational act, while changing what an
application *is* (its repository, its credentials, its control point) is a configuration one.

It refuses with 409 if the application has no trigger configured. That is deliberate: with no
trigger there is no rule admitting the ticket, and defaulting to "accept anything" would mean
the first real run was governed by an admission rule nobody wrote. Same instinct as ADR 0004's
refusal to infer `control_mode`.

### 2. The approval gate — the digest is the whole thing

ADR 0003 §6 asks for more than a record that someone clicked approve. The mechanism:

1. `engine/evidence.py` assembles a document from `flow_runs` + `flow_events`.
2. `GET /api/runs/{id}/evidence` returns it with a SHA-256 over its canonical form.
3. The decision is submitted **with that digest**.
4. The API recomputes and returns **409** if it moved.

That converts "approved run X" into "approved these exact facts about run X".

The canonicalisation is `sort_keys=True` with a fixed separator, and it lives in exactly one
function. Two implementations of "canonical form" would drift, and the symptom would be
approvals bouncing at random with no diagnosable cause.

A 409 is a normal event, not a fault — a run keeps emitting events while it waits, so a stale
page is ordinary. The UI treats it as such: it re-fetches and shows the current document
rather than surfacing an error and inviting a retry against stale evidence.

### 3. Caveats live inside the digest

The evidence document carries a `caveats` list: sandbox-graded tests, degraded isolation, no
pinned policy. Two properties matter and both are easy to lose in a redesign.

- They are **inside** the hashed document, so an approver cannot be shown a clean page while
  the qualification lives in a payload they never opened.
- The UI renders them **above** the buttons.

A caveat placed below the controls is a disclaimer written for the record rather than for the
reader.

### 4. The notification

SMTP, because the flagship deployment is air-gapped and an internal relay is the one thing
such an environment reliably has.

- **No ticket content.** Ticket text is hostile input and mail clients render more than they
  should. The ticket id is enough; everything else is behind authentication.
- **A notification, never the decision.** No approve-by-reply, no signed link that acts. The
  digest binding only works if the decision happens on a page that rendered the evidence.
- **Bcc**, so the approver roster is not disclosed to everyone on it.
- **A delivery failure is logged, not raised.** The gate still holds and nothing is released;
  losing a run because a relay hiccuped would turn a notification problem into an outage.
- Recipients come from the `users` table by role, not from a configured list. Revoking
  someone's approver role should also stop their mail; two sources of truth for "who approves"
  is one too many.

---

## The thing this session got right by refusing to paper over it

Build & Test runs the suite in **KuWarden's own sandbox**. Invariant 3 says gate verdicts read
external systems of record. The sandbox is ours. The same system produces the change and
grades it, so this is a real deviation.

There were two ways to handle it. Rename nothing and let a `CIResult` with `exit_code == 0`
be read downstream as a CI result, or make the source part of the verdict.

`CIResult.source` is now **required, with no default**, exactly like `control_mode`:

- both construction sites had to state it,
- it travels into the audit trail on a `build_test_verdict` event,
- `is_external_anchor` answers the invariant-3 question directly rather than by inference,
- and the evidence document turns it into a caveat the approver reads before deciding.

This does not fix the deviation. It makes the deviation impossible to mistake for compliance,
which is the honest available move until a CI adapter exists. Overstating what was verified is
manufacturing evidence, and for a product whose value *is* evidence that is worse than the
missing feature.

The same reasoning drove `policy_commit`. There is no `policy.yaml` loader, so there is
nothing to pin. A run records the literal string `unpinned:no-policy-loader` — chosen over
forty zeroes, which reads like a real pin to anyone scanning the column.

---

## What went wrong

**Two activity registration lists.** `notify_gate_reached` was added to `engine/worker.py` but
not to the list the tests build their worker from — and `engine/activities/__init__.py`
already exported an `ALL` the tests used, which the worker had been duplicating by hand. Three
flow tests failed after a 100-second run with `Activity function ... is not registered`.

The fix was not to add the activity in the second place. It was to delete the second place:
`ALL` is now the only list, and `worker.py` reads it. Two lists drift, and the symptom appears
minutes later in a workflow rather than at the edit.

**A test that passed without testing anything.** `test_a_dead_relay_does_not_fail_the_run`
went green in 0.13 s. `notify_gate_reached` returns early when the approver roster is empty,
so it never reached any SMTP code at all — the assertion was vacuously true. Caught by the
runtime, not by the assertion.

Now there is an `an_approver` fixture, and the "no approver at all" case is its own test that
asserts the warning is logged. **A suspiciously fast test is evidence about the test**, and a
function with an early return needs its precondition established rather than assumed.

---

## Also done

- **`tests/test_workbench_api.py`** — the API had 23 endpoints and no tests. Role guards are
  the security boundary and are applied by hand per endpoint, so the failure mode of
  forgetting one is an *open* endpoint, not an error. `test_every_endpoint_declares_who_may_
  call_it` enumerates the route table and fails on anything unguarded that is not listed in
  `public` with a written reason. Also covers: credentials never read back, revocation taking
  effect immediately via `token_version`, and sign-in not distinguishing "no such user" from
  "wrong password".
- `.env.example` documents the SMTP block, stating plainly that with no relay the gate still
  works and nobody is told.

---

## Where to pick up

1. **Move the branch push to just after the Coder.** CI cannot run on an unpushed branch, so
   this blocks the CI adapter, which blocks invariant 3 holding without qualification.
2. **CI adapter** — GitHub Actions, Azure Pipelines. Then `CIResult.source` becomes `"ci"` for
   real and the caveat disappears on its own, which is the correct way for it to disappear.
3. **The four verifier nodes are still empty.** `_verify` fans out to stubs, so
   "a verifier falsified the change" has never been reached by anything but a test.
4. **Webhook receiver**, so a ticket transition starts a run rather than a person.
5. Still open from earlier sessions and unmoved: the `control_mode` ADR 0004 deviation on
   `main`, Temporal retention versus the authority of the PostgreSQL record, `ROADMAP.md`
   contradicting ADR 0001/0002.
