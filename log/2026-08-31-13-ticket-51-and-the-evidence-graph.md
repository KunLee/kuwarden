# 2026-08-31 — A bug only a browser could find, and a prediction that was wrong

Previous: [2026-08-31-12](2026-08-31-12-findings-never-reached-the-approver.md).

---

## Context

Ticket 50 shipped and the feature did not work: clicking the user avatar produced a black page
reading *"This page couldn't load"*. The reported symptom was all an end user could give —
no stack trace, no route, no clue whether it was routing or code.

---

## What happened

### The bug was invisible in the source and obvious in a browser

Static reading eliminated the easy answers: every route the menu navigates to exists on `main`,
`AvatarBadge` is exported, and the Base UI hierarchy in `dropdown-menu.jsx` is correct
(`Portal → Positioner → Popup`). Nothing in the diff looked wrong, because nothing in the diff
*was* wrong in isolation.

Loading the deployed site and clicking the avatar produced the answer in one step:

```
Uncaught Error: Base UI error #31
  → MenuGroupContext is missing.
    Menu group parts must be used within <Menu.Group> or <Menu.RadioGroup>.
```

`DropdownMenuLabel` renders `MenuPrimitive.GroupLabel`, a group part. `UserMenu.tsx` placed it
directly inside `DropdownMenuContent`. `DropdownMenuGroup` was defined and exported in the same
file and never used.

**Every gate passed it, and each for a sound reason:** eslint — not a style question; `tsc` —
the file is untyped `.jsx` and the constraint is a runtime React context, not a type;
`next build` — the page renders, only *opening* the menu throws; four verifiers — none can run
the code; CI and Vercel — both green, and the deployment genuinely succeeded.

### `regression_risk` predicted it, in writing, and passed

From run `521065f3`, the run that shipped:

> This is the first real usage of DropdownMenu/MenuPrimitive (@base-ui/react/menu) in the app
> despite the primitive existing before; **there are no call sites/tests shown verifying the
> Base UI Menu behaves correctly in this Next.js app context** (portal rendering, focus trap,
> keyboard nav) — cannot verify from given files whether this integration was exercised.

It said nobody had ever checked that this primitive works when used. It returned `passes`. The
approver was shown `3 of 4 passed`. This is the same failure [log 12](2026-08-31-12-findings-never-reached-the-approver.md)
records, with a second worked example — and it is the strongest argument yet for the change
made there.

### Ticket 51, run as an experiment — and the prediction was wrong

The ticket carried only what a user could report, plus one line of ordinary triage:

```
Clicking user profile shows an error page. No dropdown list.
Console error on click: Uncaught Error: Base UI error #31
```

The diagnosis was deliberately **withheld** from the ticket. With it, the run would have
measured whether an agent can copy an answer.

Four outcomes were written down **before** the run finished. The prediction was **B** — a
plausible but wrong fix passing every gate — on the reasoning that the Planner's own first two
steps ("reproduce in a dev build", "look up the error reference") are precisely the two things
the sandbox cannot do, leaving it to guess from source in which the bug is invisible.

**The result was A.** One file, one attempt, sandbox green, CI-anchored, 4/4 verifiers passed:

```diff
+  DropdownMenuGroup,
...
-  <DropdownMenuLabel>Your account</DropdownMenuLabel>
+  <DropdownMenuGroup>
+    <DropdownMenuLabel>Your account</DropdownMenuLabel>
+  </DropdownMenuGroup>
```

Exactly the fix, and it left the shared `dropdown-menu.jsx` alone — the right call, and the one
the previous three attempts got wrong.

---

## What the experiment actually settled

The conclusion is sharper than either position argued before it, and it splits in two:

**Diagnosis did not need runtime access.** One line of console error substituted for it
completely. The Planner reached "improper usage of a Base UI component used outside its
required structural context" from `#31` alone, and the Coder reached the exact wrapper in a
single attempt. The earlier claim — *something has to open the menu* — is wrong as stated.

**Verification still has no anchor at all.** Nothing in the pipeline can say whether the fix
works. The sandbox greens on lint and types, the verifiers judge a diff, and the gate opens.
The only confirmation available came from a human opening the page and clicking, which is what
found the bug in the first place and what will have to confirm the fix.

So the runtime gap is real and it is in **verification**, not in **diagnosis**. That is a much
cheaper problem: it wants a preview URL in the evidence document and thirty seconds of a human,
not a browser baked into the sandbox image.

---

## Decisions

- [**ADR 0012**](../docs/adr/0012-evidence-graph.md) — the evidence graph is recorded, never
  derived; PostgreSQL rather than a graph database; nothing hosted. Its §1 states the rule the
  proposal that prompted it would have broken: **a model may not produce an evidence edge**,
  and the test for any AI-derived data is whether its output is retrieval or evidence.
- **The diagnosis stays out of ticket 51.** Writing the answer into the ticket and then
  reporting that the pipeline solved it would be manufacturing evidence about our own product —
  the failure invariant 11 exists to name.
- **The gate on run `ee66aced` is not ours to open.** The diagnosis came from this session; an
  approval from the same side is the separation of duties failing quietly.

---

## Corrections

**The prediction was wrong, and it was wrong in the direction that flatters the argument I was
making.** I had just finished arguing that this class of defect needs something to open the
menu, and I predicted an outcome consistent with that argument. The run refuted it. The value
of writing the four outcomes down beforehand is precisely that this could not be rationalised
afterwards.

**"Don't build UI testing" was right, then wrong, then right for a different reason.** Three
positions in one day: not for the intent defects (they were spec problems), then yes for this
one (it is a runtime integration defect), and now — no, because diagnosis did not need it and
verification is better served by a preview link. Each move followed a fact, but the through-line
only became clear once the experiment ran.

---

## Open

- **The fix is unverified.** Run `ee66aced` sits at its gate. Correct by inspection, green on
  every gate, and nobody has yet clicked the avatar on the deployed result. That sentence is the
  whole argument for the preview link.
- **`app/global-error.tsx` does not exist** in the reference application — no error boundary
  anywhere. Any client-side error becomes an uninformative black page, which is why the original
  report could not say more than "an error page". Unrelated to KuWarden and worth its own ticket.
- **Configuration has no history, and that is the real version of a gap I first described
  wrongly.** I called `app_config.updated_by` unable to express "an agent applied this on the
  operator's instruction". It is not a gap: the only path that writes it requires an
  authenticated principal, so it is truthful, and what I was describing was a database
  connection bypassing the product — which no column defends against.

  The gap underneath is one migration 005 already states: `flow_runs` does not record the
  configuration that governed it, so editing an application's rules silently re-interprets
  every past run under rules that were not in force, and *"the eventual fix is to pin the
  effective configuration into each run"*. `app_changes` is the compensating control and covers
  `integration_model` alone; `app_config` is overwritten in place, so the previous YAML is gone
  and "what did the tiering rules say last Tuesday" has no answer.

  Partly held already: `FlowInput.risk_rules` is passed as data at run start, so a mid-run edit
  cannot retroactively change a decision, and `risk_tier_final` records the pattern that
  matched — which is how `medium_paths 'components/**'` was readable at all today. What is
  missing is history *across* runs.

  **Deliberately not fixed now.** It becomes load-bearing when `policy.yaml` lands (invariant 8
  is `none`) or if configuration ever becomes something an agent may propose, and at that point
  pinning the effective config and making config writes append are one change rather than two.

- **The evidence graph view.** The data layer landed in this session (below); what it feeds
  has not been drawn yet. Per ADR 0012 the first view is a deliberate layout — ticket, runs as
  lanes, time along the horizontal — and whether an automatic one is ever needed is a question
  to answer against real data on a screen.

---

## Artefacts

**New** — `docs/adr/0012-evidence-graph.md`,
`log/2026-08-31-13-ticket-51-and-the-evidence-graph.md`,
`engine/db/migrations/009_run_files.sql`

**The data layer, built and backfilled.** `record_run_files` writes git's numstat — carried on
`branch_pushed` as structured data rather than left in the Push node's prose — and two
endpoints read it: `GET /api/runs/{id}/graph` and `GET /api/files/{path}/runs`. Forty historical
runs were backfilled from the GitHub API rather than from the notes, because the notes carry
"132 lines" and not an added/removed pair, and inventing the missing half to fill a NOT NULL
column would have put fabricated numbers into an evidence index.

The first query answered what the product could not answer at all a day ago:
`components/Header.tsx` has been changed by **12 runs across 4 tickets**, five of them on ticket
50 alone — at revisions r6, r2, r4, r13, r17, with the out-of-order replay visible in the
sequence.

**Changed** — `docs/adr/README.md`, `ui/src/pages/RunDetail.tsx` (`api.run` called an endpoint
that was deliberately never built; the file had not typechecked since it was written, and
`tsc -b` in CI would have caught it, so it reached `main` without CI gating it).
