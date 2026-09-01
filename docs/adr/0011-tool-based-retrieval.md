# ADR 0011 — The Coder reads the repository with tools, not with a prompt

- **Status:** Proposed — blocked on a measurement, see *Sequencing*
- **Date:** 2026-08-25
- **Depends on:** [ADR 0001](0001-flow-engine-control-plane.md), [ADR 0005](0005-sandbox-contract.md), [ADR 0010](0010-context-assembly.md)
- **Would supersede:** [ADR 0010](0010-context-assembly.md) §2 — one-shot selection. §1 and §3 survive unchanged.
- **Constrains:** `engine/nodes/coder.py`, `engine/adapters/llm/`, `engine/nodes/repo_context.py`

---

## Context

[ADR 0010](0010-context-assembly.md) settled how a model is *given* context: it names the
files it wants from a complete path listing, and the import graph expands that. It reduced
input from ~621,000 tokens per run to ~27,000, and it recorded three weaknesses it could not
fix. All three are the same weakness:

> **The model chooses its context once, before it has read anything.**

A path listing is metadata. The reference ticket said *"change the whole site design system
theme to Ocean"*; the word `ocean` lives in `app/globals.css` and `lib/site.js` and in neither
of their names. Selecting `globals.css` from its filename is a guess that a file so named
might hold theme tokens — a good guess in this repository, a worse one in any repository with
`theme.ts`, `tokens.css` and `styles/`.

And the selection is frozen for all four attempts of the inner loop. A model that discovers on
attempt 1 that it needs another file cannot have it on attempt 2. It can say so in
`reasoning`, and nothing reads that.

The alternative is what every competent engineer does and no design here has yet allowed:
**look, then look again based on what you found.**

```
grep "data-theme"          → app/globals.css, app/layout.tsx
read app/globals.css:320-360  → sees [data-theme="ocean"], and the token names
grep "SITE_THEME_COLOR"    → lib/site.js        ← a term learned by reading
read lib/site.js
edit
```

The fourth line is the whole argument. That term was not knowable from the ticket, the plan,
or any listing. No amount of better one-shot selection reaches it.

## Decision

**Give the Coder tools over its own workspace, and let it decide what to read.**

```
list_dir(path)                       structure
grep(pattern, glob?)                 find by content
read_file(path, offset?, limit?)     read, partially for large files
edit_file(path, old_string, new_string)   change, by exact replacement
```

Three properties of the existing design make this smaller than it sounds.

**The workspace already exists.** `coder()` materialises every file into a real directory
before its first model call. Tools read that directory — already populated, on the same host,
inside the same activity. This is worth stating because the objection it answers was raised
and was wrong: *"the sandbox has no network or credentials"* confuses the **container's**
isolation with the **activity's** local workspace. They are different things.

**The Coder is an activity, not workflow code.** Arbitrary I/O is permitted there. The
determinism boundary is not in play.

**The diff already comes from git.** `read_changes` reads what is on disk after the loop, and
an agent's account of what it changed is never an input to anything. So `edit_file` changes
nothing about how a change is *established* — it only changes how the model expresses one.

### `edit_file` is not the patch format that was rejected

`EDIT_SCHEMA` returns complete file contents, and its comment explains why:

> Whole-file content rather than a patch. Models produce malformed unified diffs often enough
> that the failure mode becomes "the patch would not apply", which teaches the loop nothing
> about the code.

That reasoning is about **unified diffs** — line numbers, hunk headers, context lines, all of
which a model must reconstruct and can get subtly wrong. Exact string replacement is a
different mechanism with a different failure mode: `old_string` either appears exactly once or
it does not, the tool says which immediately, and the model corrects it **in the same turn**
rather than losing an attempt.

The cost this removes is not small. A recent change of roughly forty lines required the model
to emit **923 lines** of complete file content across three files, because that is the only
way the current schema can express an edit. Output is the slow and expensive half of
generation.

## Sequencing — and why this is Proposed rather than Accepted

**Prompt caching is a precondition, not an optimisation.** The Messages API is stateless: turn
*N* re-sends turns 1..*N*−1. Input therefore grows quadratically in the number of tool calls.

| tool calls | input tokens, no caching | with caching |
|---|---|---|
| 5 | 15,000 | 4,500 |
| 10 | 42,500 | 7,000 |
| 20 | **135,000** | 12,000 |
| 30 | 277,500 | 17,000 |

For comparison, on the reference application: the whole repository is ~77,757 tokens and ADR
0010's selection is ~10,000.

So **a 20-turn tool loop without caching costs more than pasting in the entire repository** —
the exact problem this is meant to solve, reintroduced in a shape that is harder to see. With
caching it is roughly the same as today's selection while being strictly more capable.

**Caching is now implemented** (2026-08-25) and the blocker has moved from *does it exist* to
*does it work here*. `LLMRequest.cacheable_prefix` marks a stable first block; the Coder marks
the plan and the repository, which are identical across its four sequential attempts.
`Completion` carries `cache_write_tokens` and `cache_read_tokens` back from the provider, and
both are recorded per node.

They are recorded because **a cache that never hits is indistinguishable from one that works,
except on the invoice** — and it is worse than no cache, since a write costs more than an
ordinary input token while a read costs a fraction.

### The verifier half of that question is closed, and it was never empirical

*2026-08-29.* This ADR expected the fan-out to fail through a race: four verifiers fired by
`asyncio.gather` are all in flight before any has written, so all four pay the write surcharge
and none reads. The race is real and it is not the binding constraint.

The provider caches a **prefix**, and renders a request as `tools`, then `system`, then
`messages`. Each verifier's `system` ends with its own angle. The four requests therefore
diverge *before any message block is reached*, and no marker below that point can be shared
between them — not with staggering, not with a warm cache, not ever. Each verifier calls once
per pass, so a marked block would be written and never read: a surcharge on every run, buying
nothing.

**The verifiers no longer mark a prefix.** That is the second of the two remedies this ADR
already named, chosen not because a measurement showed the first was needed but because the
documented prefix rule rules the alternative out.

The change that *would* restore sharing is to move the angle out of `system` and below the
marker, into the same turn as the ticket. **Rejected:** ticket text is hostile by assumption,
and that places the instruction defining a verifier's job underneath it. A prompt cache is not
worth weakening the redaction boundary this design's credibility rests on.

**Order:** caching → measure on real runs → tools. Not tools → discover.

What the measurement must show before this is Accepted:

- The Coder's attempts 2..4 read the cache rather than re-sending. Sequential, the case most
  likely to work, and now the only open question. If it fails too, caching is unavailable in
  practice and the bounded-selection middle option becomes the right answer instead of the
  full tool loop.

### The measurement, taken

*2026-08-30, run `521065f3` on the reference application.*

```
Coder, outer attempt 1   input 1,433   cache written 19,194   cache read 57,582
Coder, outer attempt 2   input   492   cache written 18,656   cache read 18,656
run_cost                 72.79 cents
```

**The Coder's sequential attempts read the cache.** 57,582 tokens served from it against 1,433
charged at the ordinary input rate, in a node whose first attempt wrote 19,194 — three reads of
one write, which is the inner loop doing exactly what this ADR assumed it could not be trusted
to do without checking.

Both conditions are therefore met: the verifier half was closed by the prefix rule above, and
the Coder half is measured. The bounded-selection fallback is no longer the required answer.

**The status is still `Proposed`.** Accepting is one-way, and the remaining work — the
`LLMAdapter` conversation contract, the four tools, the shared confinement each of them needs,
the `grep` timeout, the loop cap — has not been done. What has changed is that nothing blocks
starting it.

## Consequences

**The `LLMAdapter` contract has to grow.** `LLMRequest` is deliberately single-shot —
`system`, `prompt`, `max_tokens`, `effort`, `schema` — and its docstring calls the narrowness a
feature. Tools require a conversation: a message list, tool definitions, tool results, and a
loop that knows when the model has stopped asking. That change lands in the one place every
provider is abstracted behind, so it must stay vendor-neutral or the abstraction was decorative.

**Every tool needs the path confinement `_apply` already has.** Ticket text is hostile by
assumption, and `read_file("../../.ssh/id_rsa")` is precisely what a successful prompt
injection asks for. `_apply` resolves and checks:

```python
if not target.is_relative_to(root):
    raise SandboxInfrastructureError(f"refusing to write outside the workspace: ...")
```

Read, grep and list need the same check, through the **same function** — the argument in
`engine/policy/globs.py`, where two implementations of one rule each kept passing their own
tests while disagreeing about what they permitted.

`grep` needs one more bound the others do not: a pattern is attacker-influenced, so a
catastrophically backtracking regex is a denial of service against the worker. Either a
timeout or a non-backtracking engine.

**The loop must be bounded.** Per [ADR 0002](0002-flow-topology.md), an unbounded loop is an
unbounded bill. A cap on tool calls per attempt, and exhausting it is a failure that says so
rather than a silent truncation.

**The audit record changes shape, and improves.** This was the one objection that survived
scrutiny, and on reflection it argues the other way. Today the trail holds one artifact: the
exact bytes sent to the model. With tools it holds a sequence — *searched for `data-theme`,
read `globals.css` lines 320–360, then read `layout.tsx`*. That is **more** legible than
123,000 tokens of context nobody will read, and it answers a question the current record
cannot: what did the agent actually look at.

The volume is real: perhaps twenty calls per attempt, four attempts. These belong in the
Coder's **notes**, not as flow events — they happen inside an activity, and the flow assigns
sequence numbers. They must be recorded **individually and not summarised**, because "the
agent read `/etc/passwd`" is a security-relevant fact that a count cannot carry.

## Alternatives considered

### Keep one-shot selection and improve the listing — a repository map

*Not rejected. Cheaper, and should probably be built first regardless.* Send each path with
its exported symbols rather than the path alone:

```
app/globals.css        [data-theme]: ember, violet, ocean, midnight, paper
components/Header.tsx  Header, navigation[]
lib/site.js            SITE_THEME_COLOR, SITE_NAME
```

~3–5 KB against 311 KB of contents — about 1.5% of the cost — and it dissolves the *Ocean*
case with no search at all, because `ocean` appears in the map. It does **not** dissolve the
frozen-selection weakness, and it cannot find a term the model only learns by reading.

It composes with this ADR rather than competing: a repository map is what the tool loop should
*start* from, so the model begins informed instead of blind.

### Keyword search over contents, from terms in the ticket

*Rejected as insufficient.* Free, deterministic, can only add files — and it only works when
the ticket and the code use the same word. "Make the site blue" against code saying `--accent`
matches nothing, and tickets are written in product language while code is written in code
language. It is a patch for the literal case, and the literal case is not the common one.

### Let the model call tools, but only during selection

*Rejected as an unstable middle.* One or two rounds of searching, then commit to a file list,
then a single edit prompt. It buys most of the capability at a bounded cost — but it keeps the
frozen selection, keeps whole-file output, and requires the same adapter change as the full
version. Having paid for the conversation, there is little reason to end it early.

**Revisit if** the measurement shows caching is unavailable or ineffective on the deployed
model, since a bounded loop is the only affordable shape without it.

### Do nothing — accept ADR 0010 as final

*Rejected, but it is a real option and the current implementation is not embarrassing.* It
cut input by 95%, it is tested, it fails loudly, and its listing rule means it cannot silently
hide a file. The case against is that its three recorded weaknesses share one cause that no
amount of tuning removes, and one of them — indirection between product language and code
language — is the normal case rather than the edge case.

## Revisit triggers

- **Prompt caching, measured.** Blocks this. If caching is ineffective, the bounded-selection
  middle option becomes the right answer instead.
- **The repository map**, which is cheaper and independent, and improves both designs.
- A customer whose repository makes even the path listing expensive — at which point the
  starting context itself needs ranking, and none of the options here supply it.
