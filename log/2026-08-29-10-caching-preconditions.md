# 2026-08-29 — The preconditions for tool-based retrieval, and a cache that could not have hit

Previous: [2026-08-24-09](2026-08-24-09-demo-hardening-and-context-assembly.md).

---

## Context

The session opened as a question rather than a task: *is there any improvement since we changed
the reference retrieval approach?*

The honest answer was no, and not because the change failed. Nothing had been measured.
[ADR 0011](../docs/adr/0011-tool-based-retrieval.md) is `Proposed` and blocked on a caching
measurement; the instrumentation to take it existed and had never met a real run — Postgres was
down, so there were no `run_cost` rows to read, and the previous session's own notes still spoke
in the future tense about what the numbers *will* tell us.

What the session then found is that "just run it" would not have worked, twice over.

---

## What happened

### The caching implementation would have died on its first real run

`_content` split the user turn into a marked prefix and a tail. The Coder's **first** attempt
has no previous failure to report, so its tail is the empty string — and the adapter sent it
anyway, as `{"type": "text", "text": ""}`. The API rejects a text block containing no text.

Every run would have failed at the Coder with a 400, which [log 09](2026-08-24-09-demo-hardening-and-context-assembly.md)
had just finished classifying as **non-retryable** — correctly, and here that means the run does
not recover. It would also have died *before* writing the cache entry that attempts 2..4 were
the entire reason for, so the measurement would have returned nothing about the thing being
measured.

Found by reading, not by running. The tests covered the two-block shape with a tail present,
which is every attempt except the one that happens first in every run.

### The verifier cache could never have hit, and the reason was documented all along

ADR 0011 predicted the fan-out would lose to a race: four verifiers fired by `asyncio.gather`
are all in flight before any has written, so all four pay the write surcharge and none reads.
It called this "an empirical question about the provider".

It is not empirical. The provider caches a **prefix**, rendered `tools`, then `system`, then
`messages`. Each verifier's `system` ends with its own angle, so the four requests diverge
before any message block is reached. No marker below that point can be shared between them —
not with a warm cache, not with staggering, not ever. And each verifier calls once per pass, so
the marked block would be written and never read: a surcharge on every run, buying nothing.

Both remedies the ADR named were still on the table. Staggering does not help, for the reason
above. Moving the angle out of `system` and below the marker *would* work, and was **rejected**:
ticket text is hostile by assumption, and that places the instruction defining a verifier's job
underneath it. A prompt cache is not worth weakening the boundary this node's credibility rests
on.

So the verifiers stop marking a prefix. The repository block went back **after** the diff, where
it sat before caching moved it to the front — the only argument for the move was the cache, and
the original argument (the change is what is under review; leading with the project buries it)
was never answered.

### The cost model was wrong by a factor of three

`pricing.py` priced Opus 5 at $15/$75 per million and Sonnet 5 at $3/$15, against $5/$25 and
$2/$10. Its own docstring says absolute accuracy is not the goal and comparability is — but the
errors were not uniform. Opus was inflated threefold and Sonnet by half, so the model-to-model
*ratios* were wrong too, and those are the part the docstring says has to survive.

This mattered more than a stale-figures footnote, because the figure's next job is deciding
whether a twenty-turn tool loop is affordable. A threefold overstatement is enough to answer
that question backwards.

### Path confinement lifted out of the Coder

`_apply`'s resolve-and-compare check is now `engine/policy/confinement.py`, with the argument
`globs.py` already makes about one rule with two implementations. One caller today; ADR 0011
adds four. The symlink case — a link inside the workspace pointing out of it, which is the case
a string-prefix check misses — now has a test, which it did not before.

---

## Decisions

- **The verifier half of ADR 0011's measurement is closed deterministically**, and the ADR says
  so rather than waiting for a run to show it. Recorded in its *Sequencing* section, which is
  legitimate while the ADR is `Proposed`.
- **No client-side cache threshold.** One was added during this session and removed in it —
  see *Corrections*. The provider applies its own minimum, for free, and it is model-dependent
  and not monotonic; a constant here can only be wrong.
- **Rates corrected and `LAST_REVIEWED` moved**, with the neighbouring models priced too — an
  absent model poisons a run's whole total, which is right, and a bad thing to trigger by
  editing one line of `kuwarden.yaml`.

---

## Corrections

**"Byte-identical across all four verifiers."** The code comment was true of the *block* and
false of the *prefix*, and only the prefix is what the provider matches on. The two are not the
same thing and the comment asserted the wrong one — which is how a design that could not work
got as far as being implemented, tested and documented.

**The gap was described as "execute a run".** That was this session's own first answer, and it
was incomplete in a way worth naming: the remaining work was not only to take the measurement
but to fix a defect that would have prevented the run from producing one. "The instrumentation
is built" and "the instrumentation works" are different claims, and only the first had evidence.

**The ADR called an answerable question empirical.** Not a fatal error — deferring to a
measurement is the right instinct and the wrong one only when the answer is already written
down in the provider's own prefix rule.

**`MIN_CACHEABLE_BYTES` was mine, and it was wrong twice over.** I added a client-side
threshold that withheld the cache marker below ~1024 tokens, justified as disambiguating
`Cache written 0`. The user asked what it actually buys. Nothing:

- Below the provider's minimum a marker is silently ignored **and not charged**, so
  withholding it saves no money.
- The record it produces — `Cache written 0, Cache read 0` — is identical to the record it
  claimed to disambiguate, because nothing anywhere says the marker was withheld. It removed
  no ambiguity; it produced the same two numbers by a different route.
- I wrote that erring low was deliberate, then set it *above* Opus 5's 512-token minimum. On
  that model it was losing cache hits outright.

Deleted, and the test that guarded it replaced by one asserting the adapter never grows the
threshold back. A client-side guess at a server-side, model-dependent, non-monotonic
constant is a liability whichever way it is set.

**Commentary trimmed at the end of the session.** Several of the comments above were written
at the length of the argument that produced them rather than the length the reader needs. The
reasoning belongs here; the code keeps one line of *why* and a pointer. Cut in
`anthropic_api.py`, `verifiers.py`, `pricing.py`, `confinement.py` — no behaviour touched, and
the suite is the proof.

---

## Open

- **The measurement itself.** Postgres came up during this session and the full suite passes
  against it, so the remaining step is one real ticket: run it, then read `run_cost` and the
  Coder's `Cache written` / `Cache read`. A single question now rather than two, since the
  verifier half is settled.
- **The ReDoS bound on `grep` and the tool-call cap.** Deliberately not built. Both are
  properties of tools that do not exist yet, and writing them now means inventing an interface
  to guard, then guarding it wrong.
- **The `LLMAdapter` conversation contract.** The real work of ADR 0011 and untouched here; it
  stays behind the measurement by design.
- ~~**`kuwarden.yaml` still declares `coder: effort xhigh`.**~~ **Resolved.** The stored
  `app_config` row governs and says `coder: medium`, `verifiers: low` — log 09's decision did
  land. The committed-example file is a stale *fallback*, inert for this deployment because
  sasagayo has a stored row, and wrong for any future application that does not. Two
  declarations of one fact, which `config_store` already warns is how this repository has been
  bitten before.

---

## Artefacts

**New**

- `engine/policy/confinement.py` — one confinement rule, ready for ADR 0011's four tools
- `tests/test_confinement.py`

**Changed** — `engine/adapters/llm/anthropic_api.py`, `engine/nodes/{coder,verifiers}.py`,
`engine/policy/pricing.py`, `docs/adr/0011-tool-based-retrieval.md`,
`tests/{test_llm_adapter,test_invariants}.py`.

Suite: **379 → 387 collected**, and for the first time this session run complete against a
live database: **386 passed, 1 skipped** (the podman-gated sandbox case). `ruff` and
`mypy --strict` clean.
