# 2026-08-24 — Demo hardening, and learning what context assembly costs

Previous: [2026-08-15-08](2026-08-15-08-repository-ci-and-secret-scanning.md).

---

## Context

A long session in the run-up to recording a demo. The intent was to find and fix anything that
would break a live run of sasagayo, the reference Next.js application.

Almost none of it was found by reading the code. Fifteen defects surfaced by *using* the
system, and they had a shape in common worth stating up front:

> **The decision was usually right, and the record failed to explain it.**

A verifier objected correctly and the trail named the wrong verifier. A tier was raised
correctly and the approver was shown the old one. A push was correctly deduplicated and the
record said it had pushed. None of these produced a wrong outcome. All of them would have
produced an unanswerable question on camera — which, for a product whose claim is the audit
trail, is the same thing as a wrong outcome.

---

## What happened

### The Coder could not see the file the ticket named

Ticket 35 asked for a fix to the top menu and the hero carousel. The Coder returned no edits
and the run died at Push with `push reached with no proposed edits`.

The cause was three nodes upstream. Context was capped at 40 files / 120 KB taken in
**alphabetical order**, and the repository had grown to 88 files. `app/` sorts before
`components/`, so `app/admin/AdminClient.tsx` (19 KB, irrelevant) was sent and
`components/Header.tsx` — the file the ticket was about — was not. The model saw the filename
in the listing, correctly refused to invent its contents, and returned nothing.

Reproduced exactly by replaying the selection against the pinned commit: `shown=30,
omitted=58, bytes_used=119,955`, identical to the run's own record.

The cap was removed. That fixed the correctness problem and created a cost problem, which took
the rest of the session to understand properly.

### The sandbox was grading nothing

`test_command` was `[sh, -c, "python -m pytest -q; test $? -le 5"]` on a **Python** image,
against a **TypeScript** repository. pytest collects nothing, exits 5, and `test 5 -le 5`
converts that to a pass. Every change passed Build & Test regardless of content.

So the Coder's inner loop — the feedback edge ADR 0002 replaced a linear pipeline to get — had
been running against a check that could not fail. CI was the only real gate, a whole outer
attempt away, and its failures never reached the model.

Fixed by building a Node 20 toolchain image with the application's own dependencies baked in
(no egress in the sandbox, so they cannot be installed at run time) and pointing
`test_command` at `npm run lint && npm run typecheck`. Verified both directions inside the
real sandbox: the agent's code exits 1 naming `react-hooks/set-state-in-effect`, the corrected
code exits 0.

`next build` is deliberately absent: Next 16's Turbopack reserves multi-gigabyte WebAssembly
ranges and dies on the sandbox's `ulimit -v` regardless of the change under test. CI still
runs it as the independent anchor.

### Three defects in how a rejection was recorded

Found while answering "why was this blocked". All three in the same seam:

1. **`aborting` named the wrong verifier.** The handler recomputed the failing set from
   `self._latest`, and the verifier fan-out leaves that holding whichever of four parallel
   activities replied last. A run rejected by `correctness`, `security` and `regression_risk`
   recorded `falsified_by: ["test_evidence"]` — the one verifier the operator had deliberately
   disarmed, and therefore the only one that could not have caused it.
2. **Compensate saw one verifier's brief**, so three sets of findings were destroyed rather
   than recorded.
3. **`verifier_verdict` had never fired for any run in the system's history.** `_node_step`
   drained `result.notes` unconditionally — including under `record=False`, where the flag
   means *the caller emits these* — so `_verify`'s `if result.notes` guard was always false.

The third is the one worth remembering: a feature that had never once worked, in a table of
events nobody had noticed was missing.

### `retry_count` was clobbered, and pushes were silently discarded

The outer ③⇄④ loop set `state.retry_count = attempt`; the Coder's inner loop immediately
overwrote it from 0. `retry_count` is the `kuwarden-attempt` commit trailer, and that trailer
is the SCM adapters' idempotency key.

So pass 2 produced a byte-identical commit message, the adapter matched it against the branch
tip, concluded the push had already landed, and returned without pushing. The run then read CI
back for the *previous* commit, got the previous failure, and looped — grading the first
attempt's code until the retry budget ran out, while Push's own notes claimed it had pushed
four files.

Split into `retry_count` (inner) and `push_attempt` (outer).

**`test_push.py` passed throughout**, because it set the counter by hand and proved the
adapter extends a branch when the counter changes. Nothing proved the counter changed. The
regression test drives the real flow instead.

### Build & Test was grading three files in an empty directory

Exposed immediately by making the sandbox real. Build & Test materialised only
`state.proposed_edits` — the changed files and nothing else — so eslint exited 2 with
"couldn't find eslint.config.js". The flow read that as *the change is broken* and sent it back
to a Coder who could not possibly fix it.

Invisible for as long as the test command was pytest-on-nothing, which succeeds whatever the
directory holds. Now the tree is re-read at the pinned base commit with the edits laid over
it.

### Verifiers were rejecting valid changes they could not see

Ticket 38 asked to switch the site theme to Ocean. The Coder set `data-theme="ocean"` and
changed nothing else — correct, because `[data-theme="ocean"]` already existed at
`app/globals.css:331`. Two verifiers blocked it:

> globals.css is not among the changed files, so there is no evidence the Ocean color tokens
> actually exist

Both reasoned soundly. Neither could open the file. Invariant 4 gives verifiers the diff and
the ticket, and nothing else.

Fixed by giving them the repository at the pinned base commit. This does not weaken invariant
4: what that redaction protects against is a verifier seeing the *Coder's reasoning* — its
plan, its retry count, the other verdicts. A public commit is not reasoning; it is what any
reviewer opening the pull request would have. There is a test asserting both halves.

### The cost of fixing all that

Measured after the fact, which was the mistake:

```
planner                    556 input tokens
coder                  123,298
verifier ×4            124,283 each
                     ─────────
per run                ~621,000 input tokens, against ~4,000 of output
```

Five calls each reading an entire repository to produce a few hundred tokens of edit. Both
changes that caused it were correct fixes to real bugs; both were the blunt version.

The user's objection was the right one and was made twice before it landed: *the change is
only one file, why does it need 75,000 tokens of context?*

### Context assembly, properly

Rebuilt as two stages — [ADR 0010](../docs/adr/0010-context-assembly.md):

1. One cheap call: the plan plus a complete path listing, and the model returns the paths it
   needs.
2. Deterministic expansion along the import graph — **forward** (what those files import) for
   everyone, and **backward** (what imports the changed files) for verifiers, because
   `regression_risk` cannot answer *what else does this break* from the changed files alone.

Measured on the reference application: 83 files / 311,029 bytes becomes 3–6 files /
12,000–21,000 bytes. **93–96% less.**

The backward index immediately justified itself: a change to `ArtistCarousel` yields
`app/page.tsx` and `app/discover/page.tsx` — precisely the two files `correctness` had
speculated about, and could not check, when it blocked ticket 35.

---

## Decisions

- [**ADR 0010**](../docs/adr/0010-context-assembly.md) — the model chooses its own context,
  expanded along the import graph, and the listing is never truncated.
- **`toolchain_image` and `test_command` fail closed.** They used to default to a Python image
  running `pytest -q`, so any application that declared no `sandbox:` block was graded by
  pytest whatever language it was written in. That is invariant 11's failure aimed at the
  reality anchor: never inferred, never defaulted.
- **A terminated run is its own outcome** (migration 008). Not `aborted`, which is the flow
  stopping *itself* on the evidence. A terminate skips compensation, so the branch survives
  and the record says which one.
- **Coder effort lowered `xhigh` → `medium`**, affordable only because the sandbox now runs a
  real check: a weak attempt is falsified in ninety seconds inside the Coder's own loop rather
  than reaching a pull request.

---

## Corrections

Mine, mostly, and several were caught by the user rather than by me.

**"The API isn't running."** It was, on 8080. I had assumed 8000 and reported a fault as fact.
Corrected within the same turn, but the assumption should not have been stated as a finding.

**"The worker has no open connection."** It did. My `netstat` filter was broken by column
padding, and I used it to argue a run was hung when it was working normally. Twice I told the
user a run was fine when I had not checked hard enough, and once I told them it was broken
when it was not.

**The symlink that broke every run.** My first `test_command` linked `/opt/deps/node_modules`
into the workspace. It worked inside the container and broke everything after it: the link
also lands in the *host* directory `changed_files` reads, where `/opt/deps` does not exist, so
`git add -A` failed with `unable to index file 'node_modules'` and runs died just after the
Coder. Fixed by installing to `/node_modules`, which Node finds by walking up, so nothing is
written into the workspace at all.

**The conftest change that consumed the edit sequence.** Adding the selection pass, I taught
the fake platform to answer it by calling `coder_edits_factory` — which is a *per-attempt
sequence* (break it, then fix it). The selection call ate one, so the loop under test received
the wrong attempt's edits and four assertions shifted by one. Precisely the bug class those
tests exist to catch, caught by those tests.

**Two of my three objections to tool-based retrieval were wrong**, and the user pushed until I
checked. "The sandbox has no network or credentials" confused the container's isolation with
the activity's local workspace — the Coder materialises the whole repository to disk before its
first model call, so tools would read an already-populated local directory. "Workflow code must
be deterministic" ignored that the Coder is an *activity*. Only the audit objection survived,
and on reflection it argues the other way: a tool transcript shows the working.

**"Filenames are enough to select context."** They are not, and the user said so before I did.
Ticket 38 said *"change the whole site design system theme to Ocean"*; the word `ocean` lives
in `app/globals.css` and `lib/site.js` and in neither of their names. Recorded as a known
weakness in ADR 0010 rather than papered over.

**An unpaid account returns HTTP 400.** Anthropic reports an exhausted credit balance as
`invalid_request_error`, so the most likely cause of a rejected request in practice was being
retried three times as though it were transient — and the adapter discarded the provider's
message entirely, making every 400 read identically. Now logged to the worker (not the audit
trail, which would quote ticket text) and classified non-retryable.

---

## Open

**The repository map.** Paths plus each file's exported symbols — ~3–5 KB against 311 KB —
would dissolve the "filenames are metadata" weakness with no search at all. The cheapest
remaining win and the next thing to build.

**Prompt caching, unmeasured.** Four inner attempts and four verifiers re-send substantially
the same context. This should be measured *before* further selection work, because it may
reprice every option in ADR 0010.

**Tool-based retrieval.** The intended successor. Its own ADR, since the interesting question
is whether a twenty-step tool transcript is still evidence a regulator can read.

**Verifier findings do not feed back into the Coder.** CI failures now do; a `correctness`
rejection still ends the run, and a re-run starts from scratch and may reproduce the same
mistake.

**The ticket only hears twice** — picked up, and the outcome. The middle is missing: verified,
awaiting approval. A new external-effect path, so it needs the same marker-based idempotency
the acknowledgement uses.

**Trigger health is invisible.** A dead webhook produces silence, which is indistinguishable
from nobody filing a ticket. The subscription went stale twice in one day (a quick tunnel
mints a new hostname on every restart) and both times it was found by waiting and then asking.
A `last_delivery_at` on `app_triggers` turns a silent failure into a visible one.

**A merged branch from a terminated run is invisible to the record.** Run `81aa3d96` was
terminated, its branch survived by design, and a human merged it. KuWarden's record says
`terminated`; the code is in production. The commits carry `kuwarden-run-id`, so a
reconciliation could find every change KuWarden produced that reached production outside its
control — gap 12 with a concrete answer.

---

## Artefacts

**New**

- `docs/adr/0010-context-assembly.md`
- `engine/nodes/repo_context.py` — one renderer, the import graph, both directions
- `engine/sandbox/recipes/node20/Containerfile` and `build-app` — per-application toolchain images
- `engine/db/migrations/008_run_terminated.sql`
- `tests/test_coder_context.py`

**Changed** — `engine/flows/delivery.py`, `engine/nodes/{coder,verifiers,build_test,push,triage,reporter,compensate}.py`,
`engine/adapters/llm/{__init__,anthropic_api}.py`, `engine/adapters/{protocols,ticket/*,scm/*}.py`,
`engine/{config,state,evidence}.py`, `engine/api/main.py`, `engine/sandbox/{podman,workspace,__main__}.py`,
`ui/src/components/{FlowGraph,ui}.tsx`, `ui/src/pages/RunDetail.tsx`, `ui/src/{api,types}.ts`.

Suite: **339 → 363 tests**, one skipped (podman-gated).
