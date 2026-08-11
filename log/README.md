# Working log

A record of how this project actually got built — the path, not just the destination.

## Why this exists alongside `docs/adr/`

The two are deliberately different, and the distinction mirrors one the architecture itself
makes ([ADR 0003](../docs/adr/0003-role-graph-and-traceability.md)):

| | `docs/adr/` | `log/` |
|---|---|---|
| Analogy | The **role graph** — slow, curated, authoritative | The **work graph** — what actually happened |
| Records | Decisions that survived | The path, including what didn't survive |
| Immutable? | Yes, once accepted | Yes, append-only |
| Audience | Anyone joining the project later | Us, next week |

An ADR records *what was decided and why*. The log records everything an ADR deliberately
leaves out:

- questions that were asked and how they were resolved,
- **things that turned out to be wrong**, and who caught them,
- options considered and dropped before they were formal enough to warrant an ADR,
- what was deferred, and what triggered the deferral.

The third bullet is the one that earns the directory. A project's ADRs make it look like every
decision was reached cleanly on the first attempt. They never were, and the corrections are
usually more instructive than the conclusions.

## Reading this later

The append-only rule means an entry that turned out to be wrong **stays wrong on the page**.
That is correct for a journal and unhelpful for onboarding, so the corrections are resolved in
[docs/KNOWLEDGE_BASE.md](../docs/KNOWLEDGE_BASE.md) — a reconciled view of what is true now,
rewritten as things change.

Read that first. Come here when you want to know *how* something came to be, or what was
tried and dropped.

## Format

One file per working session: `YYYY-MM-DD-NN-topic.md`.

Each entry covers:

| Section | Content |
|---|---|
| **Context** | Where we were starting from |
| **What happened** | The substantive thread, in order |
| **Decisions** | What was settled, with links to the ADR if one was written |
| **Corrections** | What was wrong and got fixed — stated plainly, including mine |
| **Open** | What was left unresolved, and why |
| **Artefacts** | Files created or changed |

## How entries get written

**Honestly: not automatically, unless a hook is configured.**

Claude can only write files during its own turns. Without harness-level automation, the log
depends on it remembering — which is not a dependable guarantee, and a log people stop
trusting is worse than no log.

Two levels, and they are complementary:

1. **Curated session entries** (these files) — written by Claude at the end of a substantive
   working session. Readable, greppable, and the thing anyone will actually use.
2. **Raw transcript capture** (optional) — a `Stop` hook in `.claude/settings.json` appending
   the full transcript to `log/raw/`. Enforced by the harness, so it cannot be forgotten.
   Complete but verbose; nobody reads it, and that is fine — it exists for the day someone
   needs to reconstruct exactly what was said.

If raw capture is enabled, `log/raw/` should be reviewed before the repository is made public
or shared: transcripts capture everything discussed, including material that was never
intended as project documentation.

## Conventions

- **Append, never rewrite.** If an earlier entry turns out to be wrong, say so in a later
  entry rather than editing history. The point of the record is that it shows what was
  believed at the time.
- **Record corrections in full.** An entry that only lists successes is not worth keeping.
- **Link, do not duplicate.** If something became an ADR, link the ADR and keep the log entry
  to the reasoning that led there.
