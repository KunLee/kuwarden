# 2026-08-31 — I broke the worker, and the guard I had just written could not see it

Previous: [2026-08-31-13](2026-08-31-13-ticket-51-and-the-evidence-graph.md).

---

## Context

Ticket 52 was filed and nothing happened. The Azure DevOps subscription showed deliveries
arriving. No run appeared.

---

## What happened

### The cause was mine, and it was three hours old

Earlier in the session I added `record_run_files` to the delivery flow, which meant
`delivery.py` grew an import of `ChangedFile` from `engine.activities.audit`. The worker was
running at the time. It reloaded `delivery.py` and did not reload `audit.py`, so it held a
workflow definition importing a name its own copy of the activities module did not have.

```
cannot import name 'ChangedFile' from 'engine.activities.audit'
```

Every workflow task failed on that import, at the sandbox's `create_instance`, before any
activity was scheduled. The code on disk was consistent the whole time — `mypy --strict` and
the full suite passed. **The inconsistency was only in the running process**, which is the one
place neither of those looks.

Three workflows for work item 52 sat `RUNNING` in Temporal with no `flow_runs` row and a
workflow task on attempt 10.

### The guard I wrote this morning was blind to exactly this

`_superseded_by` refuses a delivery whose revision is not newer than the highest already
launched for that work item. It read `flow_runs`.

`flow_runs` rows are written by an **activity**. With the worker unable to run a workflow task,
no row was ever written, the guard saw nothing, and all three replayed revisions — r2, r4, r6,
arriving out of order again — were admitted.

**The check depended on the component that was broken.** That is the whole lesson, and it is
not a new one in this repository: a control whose evidence is produced downstream of the
failure it guards against is not a control.

### Fixed by moving the record upstream of everything

[Migration 010](../engine/db/migrations/010_trigger_deliveries.sql) adds `trigger_deliveries`,
written **by the hook itself**, before Temporal is dialled. It carries the work item, the
revision, whether a run started, and the reason when one did not.

That single row closes two things at once:

- **The guard no longer depends on the worker.** It reads the deliveries it wrote, and still
  reads `flow_runs` for runs predating the table.
- **Trigger health stops being invisible** — [log 09](2026-08-24-09-demo-hardening-and-context-assembly.md)
  asked for this and three separate investigations have since started by asking whether a
  webhook was even connected. A refusal is now a fact with a reason attached:
  `does not carry the 'kuwarden-auto' tag` ends that investigation in one query.

The endpoint's work item and revision are now read **before** the admission checks rather than
after, so a refusal can name what it refused. A record that cannot say which ticket was turned
away is barely a record.

`test_every_hook_exit_records_the_delivery` reads this module's source and fails if any exit
answers without going through `_delivered`. The property wanted is "somebody adding a new
admission rule cannot forget", and no runtime test covers a branch that does not exist yet.

### Recovery

Terminated r2 and r4 in Temporal as superseded — leaving them would have restarted the ticket
50 incident, three concurrent runs from one base, the moment a healthy worker appeared. Started
a correct worker alongside the stale one; the retries kept landing on the stale one, so it was
stopped. Work item 52 then began normally and reached Build & Test.

The operator's own worker terminal is left exited rather than restarted — it is theirs, and a
process this session started should not silently become the thing production depends on.

### And 102 abandoned applications

Separately, the Workbench was making a hundred `/triggers` calls per page load. The test suite
had left **102 applications** in the operator's database, with their runs, events, credentials
and configuration.

The session teardown had been failing since long before tonight, for a reason worth writing
down: `app_changes` is append-only by trigger and cascades from `app_registry` — and **a
cascade still fires row triggers**. The trigger raised, the whole purge transaction rolled
back, and the only symptom was one warning printed under `-q`, where nobody reads it.

`run_files` would have broken it a second way from today, having no cascade of its own.

Both fixed, the purge now covers every table it touches, and a failure now prints a screen of
`!` instead of a line nobody sees.

---

## Corrections

**The outage was mine.** Editing a workflow definition's imports while a worker is running is
enough to wedge every workflow it starts, and nothing in the local checks catches it because
the fault is in a process, not in a file.

**The guard was written this morning and was already wrong.** Its docstring said it was "not a
lock" and named the race it could not cover. It did not name the case that actually happened,
which is that its only evidence is written by the thing it is guarding against being down.

---

## Open

- **A worker running stale code is undetectable from outside.** It polls, it accepts tasks, it
  fails all of them. Nothing reports "this worker's modules disagree with the repository". A
  build id or a source hash reported at startup, and visible in the Workbench, would turn a
  three-hour outage into a glance.
- **The operator's worker terminal is exited** and wants restarting; the background worker this
  session started is not a permanent arrangement.

---

## Artefacts

**New** — `engine/db/migrations/010_trigger_deliveries.sql`,
`log/2026-08-31-14-i-broke-the-worker-and-the-guard-was-blind.md`

**Changed** — `engine/api/main.py` (`_delivered`, the reordered extraction, a supersession
check that no longer depends on the worker), `tests/conftest.py` (the purge that had been
failing silently), `tests/test_workbench_api.py`, `kuwarden.yaml`.

Suite: **395 passed, 1 skipped**. `ruff`, `mypy --strict`, `tsc` and `npm run lint` clean.
