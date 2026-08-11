# Troubleshooting a run

Six surfaces, and knowing which one answers which question saves most of the time. The common
mistake is to reach for logs first — for a KuWarden run they are the *least* informative of
the six, because the record of what happened is the audit trail, not a log line.

---

## The six surfaces

| # | Surface | Answers | Where |
|---|---|---|---|
| 1 | **Preflight** | *Is this even configured to work?* | `pwsh -File scripts/dev-up.ps1 -NoStart` |
| 2 | **Worker window** | *Which node is running, which one broke, how long each took* | the `kuwarden worker` terminal |
| 3 | **Temporal UI** | *What exactly was passed to that activity, what came back, why it retried* | http://localhost:8233 |
| 4 | **Audit trail** | *What is on the permanent record* | Workbench `/runs/{id}`, or `flow_events` |
| 5 | **Database** | *What is actually registered and stored* | `psql`, or the queries below |
| 6 | **Browser devtools** | *Anything where the UI looks wrong* | F12 |

### 1. Preflight — always start here

```bash
pwsh -File scripts/dev-up.ps1 -NoStart
```

Prints the configuration the worker will load, and the accounts that exist. Most "the run did
nothing" reports are a configuration mismatch this catches in two seconds.

### 2. Worker window

Every node logs through one choke point, and **every line carries the run id** — a worker
serves many runs concurrently and their activities interleave:

```
run 0912a8ad-… | triage     | started
run 0912a8ad-… | triage     | ok in 0ms
run 0912a8ad-… | planner    | ok in 1016ms      ← the first model call
run 0912a8ad-… | coder      | ok in 437ms
run 0912a8ad-… | push       | ok in 16ms
run 0912a8ad-… | build_test | ok in 296ms
run 0912a8ad-… | coder      | FAILED after 812ms: ConfigError: … ← what broke, and where
```

Filter one run out of the noise:

```bash
grep "run 0912a8ad" worker.log
```

`KUWARDEN_LOG_LEVEL=DEBUG` before starting the worker for more.

**What the worker window is not**: the record. These lines are operational and disposable.
Anything that matters as evidence is in the audit trail — see §4.

### 3. Temporal UI — the deepest view

http://localhost:8233 → your workflow → **History**.

This is the one people forget, and it answers what nothing else can:

- the **exact input** each activity received and the **exact value** it returned
- the **full stack trace** of a failure, not just its message
- **how many times** it retried and why it stopped
- **where a run is parked** right now, including waiting at an approval gate

A run that "hangs" is almost always visible here as an activity that is scheduled but has no
worker to pick it up — which means the worker died, or is on a different task queue.

### 4. Audit trail — what is on the record

The Workbench run page, or directly:

```sql
SELECT seq, kind, node_id, payload FROM flow_events WHERE run_id = '…' ORDER BY seq;
```

`flow_events` is append-only, enforced by a database trigger (invariant 9). If a fact matters
after the fact, it is here — not in a log. Particularly:

- `build_test_verdict` — the exit code, **and `source`**: `sandbox` or `ci`. Check this before
  concluding anything from a green run
- `branch_pushed` — branch, commit, attempt number
- `sandbox_isolation` — whether the host actually enforced the limits
- `gate_reached` / `gate_passed` / `gate_rejected`

### 5. Database — what is really configured

```bash
uv run python -c "
import asyncio
from engine.devenv import load_dotenv
from engine.db import connect
async def m():
    load_dotenv()
    async with connect() as c:
        for a in await c.fetch('SELECT id, name, repo_url, integration_model FROM app_registry'):
            print(a['name'], a['repo_url'], a['integration_model'])
            print('  credentials:', [r['kind'] for r in await c.fetch('SELECT kind FROM app_credentials WHERE app_id=\$1', a['id'])])
            print('  triggers   :', [dict(r) for r in await c.fetch('SELECT provider, organisation, project, label FROM app_triggers WHERE app_id=\$1', a['id'])])
asyncio.run(m())
"
```

### 6. Browser devtools

For anything visual. Computed styles are the fast way to tell "the class is missing" from
"the class is there and resolves to transparent" — those look identical on screen and have
completely different causes.

---

## Symptom → surface

| Symptom | Look at | Usual cause |
|---|---|---|
| Started a run, nothing happens | **3**, then **2** | The worker is not running, or never printed `worker ready` |
| A button appears to do nothing | **6** | It is `disabled`, or the error banner rendered off-screen |
| `no credential for scm.read …` | **5** | A credential slot was never filled |
| `… does not carry the 'kuwarden-auto' label` | — | Working as designed. Admission control |
| Rejected after 4 attempts, tests never passed | **2**, then **4** | `pytest` exits 5 on an empty collection — see the `test_command` note in `kuwarden.example.yaml` |
| `the repository is empty` | — | The Coder pins a base commit first; push any commit |
| Probe says "not achievable" | its own detail text | Informational only. It does **not** block a run |
| A green run you do not trust | **4** | Check `build_test_verdict.source`. `sandbox` is not an independent check |
| `… encrypted with key X, but KUWARDEN_SECRET_KEY is key Y` | **1** | `.env` changed after credentials were stored. Re-enter them |

---

## What is missing, so you do not go looking for it

- **No structured logging.** Plain `logging`, no JSON, no aggregation. Fine on a laptop;
  a customer deployment will want log shipping and this is not built.
- **Nodes log start/finish/failure and nothing else.** No per-model-call logging, no token
  counts, no prompt capture. The Temporal history has the activity inputs and outputs, which
  covers most of it — but there is no way to see the exact prompt a model received.
- **The API has no request logging** beyond uvicorn's access line.
- **No metrics.** Nothing counts runs, durations, or failures over time. The evaluation
  metrics CLAUDE.md asks for — PR merge rate, human minutes per run — have nowhere to come
  from yet.
