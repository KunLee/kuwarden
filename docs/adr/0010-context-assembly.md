# ADR 0010 — The model chooses its own context, and the record says what it saw

- **Status:** Accepted
- **Date:** 2026-08-24
- **Depends on:** [ADR 0001](0001-flow-engine-control-plane.md), [ADR 0002](0002-flow-topology.md), [ADR 0005](0005-sandbox-contract.md)
- **Constrains:** `engine/nodes/repo_context.py`, `engine/nodes/coder.py`, `engine/nodes/verifiers.py`

---

## Context

Every generative node needs some of the repository in front of it, and "some" is the entire
problem. The Coder cannot edit a file it has not seen. A verifier cannot judge whether a
change is right without the code it changed. Neither can be sent a large repository on every
call at a price anybody would pay.

This was decided badly three times in one day, and the sequence is the useful part of this
record because each failure was invisible in a different way.

**A cap, alphabetical.** 40 files or 120 KB, whichever came first, taken in sorted order. On
an 88-file application this sent `app/admin/` to a ticket about `components/Header.tsx`,
because `app` sorts before `components`. The model was shown the filename in a listing, was
correctly unwilling to invent its contents, and returned no edits. The run then failed three
nodes downstream at Push with `push reached with no proposed edits` — a message naming neither
the file, the cap, nor the reason. **The defect was not the cap's size. It was that a cap
does not choose less context; it chooses an arbitrary subset, and says nothing.**

**No cap at all.** Every text file, in full. Correct, and immediately measurable: ~123,000
input tokens per Coder call and per verifier call, five calls a run, against 733–6,117 tokens
of output. ~621,000 tokens of input to produce a few hundred of edit. The models were not
writing; they were re-reading a codebase nobody had changed.

**Diff-only, for verifiers.** Cheap and wrong in the other direction. Asked to switch the site
theme, the Coder set `data-theme="ocean"` and changed nothing else — correct, because
`[data-theme="ocean"]` already existed at `app/globals.css:331`. Two verifiers blocked it:
*"globals.css is not among the changed files, so there is no evidence the Ocean color tokens
actually exist"*. Both reasoned soundly from what they had. Neither could open the file.

Those three failures bound the problem from both sides. Too little context produces confident
verdicts about the reviewer's own blindness. Too much costs twenty times what the work is
worth. And an *arbitrary* selection is worse than either, because its failures are silent.

## Decision

**Context is selected by the model, expanded deterministically, and never silently truncated.**

Three rules, in priority order. The first is the one the others exist to protect.

### 1. The listing is always complete; only contents are selective

Every path in the repository is always in the prompt. Contents are not. The prompt states how
many files were withheld and that more can be requested:

> Contents are shown for the selected ones only: 74 were not selected — say so in `reasoning`
> if you need one and it will be provided.

This is what makes selectivity safe. A model that is shown a partial repository and told
nothing concludes the missing files do not exist — which is exactly what produced the
`globals.css` rejection above. A path costs nothing; the whole listing for an 88-file
application is under 2 KB.

### 2. Selection is asked for, never inferred

The Coder makes one cheap call — the plan plus the listing — and returns the paths it needs.
Verifiers do not need the call: their seed is the set of paths in the diff.

The rule is not "choose a better heuristic". It is that **no heuristic may decide what a model
sees**, because every one of them can silently omit the file the ticket is about, and this
system's central claim is that its record explains its decisions.

### 3. The selection is expanded along the import graph, in both directions

Static, deterministic, no model, and therefore free and replayable.

- **Forward** — what the selected files import, two hops. Answers *does the thing this refers
  to exist*. Selecting `components/Header.tsx` also yields `SearchPalette`, `AdminNavLink`,
  `ui/avatar` and `lib/utils`.
- **Backward** — the files that import the changed ones, capped. Answers *what else does this
  break*, which is `regression_risk`'s entire job and is unanswerable from the changed files:
  a changed prop is invisible in the file that changed and obvious in the file that uses it.

Expansion exists so that a selection need not be perfect. The model names what it is working
on; the graph supplies what it is working *against*.

Measured on the reference application: 83 files and ~311,000 bytes becomes 3–6 files and
~12,000–21,000 bytes. A 93–96% reduction, with the callers correctly identified — a change to
`ArtistCarousel` brings `app/page.tsx` and `app/discover/page.tsx`, the two files a verifier
had previously speculated about and could not check.

## Consequences

**The record gains a row that explains most wrong changes.** "The model asked for 3 files, 9
after imports, 74 listed but not sent" is the first thing to read when a change is wrong for
no visible reason.

**An unusable selection falls back to the whole repository and says so.** Expensive is the
correct failure here; a Coder editing a repository it cannot see is the defect this replaced.
`the model selected no known file; sending the whole repository` goes in the notes, because a
run that fell back and one that selected well are otherwise indistinguishable until the
invoice arrives.

**One prompt per attempt remains one recorded artifact.** The audit trail holds the exact
bytes the model was given. This property is worth more here than in most systems, and it is
the property the next step trades away.

**Import parsing is a regex, not a parser.** It misses dynamic `import(variable)`, re-export
chains past two hops, and tsconfig path aliases other than `@/`. Approximation is acceptable
*only because* rule 1 holds: the file is still listed, and the model is told it may ask.

## Known weaknesses of this decision

Recorded deliberately, because the next ADR will be about fixing them and it should not have
to rediscover them.

**Filenames are metadata; the answer is in the contents.** The reference ticket said *"change
the whole site design system theme to Ocean"*. The word `ocean` appears in `app/globals.css`
and `lib/site.js` and in neither of their names. Selecting by path listing is a guess that a
file called `globals.css` might hold theme tokens — a good guess here, a worse one in a
repository with `theme.ts`, `tokens.css` and `styles/`.

**The selection is frozen for every attempt of the inner loop.** It is computed once, before
the loop. A model that discovers on attempt 1 that it needs another file cannot have it on
attempt 2; it can say so in `reasoning`, and nothing reads that.

**It inherits the quality of the plan.** The Planner is told to produce "a plan another
engineer could follow" and does — including steps like *"reproduce the issue locally"* and
*"check the console for errors"*, which the Coder cannot perform, having no browser and no
running application. A plan that never names a file gives the selection nothing to work with.

## Alternatives considered

### Send the whole repository

*Rejected on cost, having been implemented and measured.* ~621,000 input tokens per run
against ~4,000 of output. It is the only option with no correctness risk at all, which is why
it remains the **fallback** when selection fails rather than being deleted.

**Revisit when** prompt caching is in use and the repository is small: for a 30-file project
the difference may not be worth the extra call.

### A size or file-count cap

*Rejected permanently.* Not because 40 files was the wrong number, but because a cap chooses
an arbitrary subset and reports nothing. Its failure mode is a run that dies three nodes later
with a message naming neither the missing file nor the cap. No number fixes that.

### Keyword search over file contents, from terms in the ticket

*Rejected as insufficient, not as harmful.* It would have found `globals.css` from the word
"Ocean", and it is free, deterministic, and can only add files. But it only works when the
ticket and the code use the same word. A ticket saying "make the site blue" against code
saying `--accent` matches nothing, and tickets are written in product language while code is
written in code language — so the miss is the normal case, not the edge case.

**Revisit if** tool-based retrieval is not adopted: as a union with the model's selection it
is a strict improvement over selection alone, at no token cost.

### Embeddings and semantic search over the repository

*Rejected.* It needs an embedding model — a second model dependency, and one that must run
locally in an air-gapped deployment — plus an index that goes stale against a moving branch
and must be invalidated per commit. The cost is a permanent piece of infrastructure; the
benefit over the model choosing for itself is unproven.

**Revisit when** a customer's repository is large enough that the listing alone is expensive.
A listing is ~2 KB for 88 files; at 50,000 files it is not, and that is the point where
ranking has to come from somewhere other than reading the whole index.

### A repository map — paths plus the symbols each file exports

*Not rejected. Deferred, and it is the most promising cheap improvement.* Instead of a bare
path listing, send each path with its top-level exports:

```
app/globals.css      [data-theme]: ember, violet, ocean, midnight, paper
components/Header.tsx  Header, navigation[]
lib/site.js          SITE_THEME_COLOR, SITE_NAME
```

For the reference application that is roughly 3–5 KB against 311 KB of contents — about 1.5%
of the cost — and it dissolves the "filenames are metadata" weakness above without any search
at all: the Ocean ticket would match `ocean` in the map. It composes with everything else,
including tools, because it lets a model *start* informed rather than blind.

**Revisit immediately after the current implementation is stable.** This is the next thing to
build if tool-based retrieval is delayed.

### Tools — `grep`, `read_file`, `list_dir` — driven by the model

*Not rejected. This is the intended successor, and it is deferred only for sequencing.*

Three objections were raised against it and two of them were wrong:

- *"The sandbox has no network or credentials."* **Wrong, and it confused two different
  things.** The Coder materialises the entire repository into a workspace directory before its
  first model call. Tools would read that directory — already populated, on the same host,
  inside the same activity. The container's isolation is a separate question from the
  activity's local workspace.
- *"Workflow code must be deterministic."* **Wrong.** The Coder is an activity. Arbitrary I/O
  is permitted there; that is what activities are for.
- *"One prompt is one recorded artifact; a tool transcript is a reconstruction."* **Correct,
  and on reflection it argues the other way.** "Searched for `data-theme`, read
  `globals.css` lines 320–360, then read `layout.tsx`" is more legible evidence than 123,000
  tokens of context nobody will read. It shows the working.

Two real constraints remain and belong in that ADR:

1. **A tool loop is not automatically cheaper.** Each turn re-sends the conversation, so an
   unbounded loop grows quadratically and can cost more than one large prompt. What makes it
   cheap is prompt caching plus a bounded loop — and per [ADR 0002](0002-flow-topology.md), an
   unbounded loop is an unbounded bill regardless.
2. **Every tool needs the path confinement `_apply` already has.** Ticket text is hostile by
   assumption, and `read_file("../../.ssh/id_rsa")` is what a successful prompt injection asks
   for. It must be the *same* function, not a second copy — the argument in
   `engine/policy/globs.py`.

**Revisit when** the current implementation has been stable through a set of real runs, and
prompt caching has been measured.

### Prompt caching

*Not an alternative — an orthogonal lever, and not yet used.* Four inner attempts and four
verifiers re-send substantially the same repository context. Marking that block cacheable
would cut the repeated cost without changing what is selected at all. It should be measured
before any further selection work, because it may move the economics enough to change which
of the options above is worth building.

## Revisit triggers

- The repository map, once the current implementation is stable — the cheapest remaining win.
- Tool-based retrieval, as the successor to one-shot selection.
- Prompt caching, measured first, because it may reprice every option here.
- A repository large enough that the complete listing is itself expensive.
