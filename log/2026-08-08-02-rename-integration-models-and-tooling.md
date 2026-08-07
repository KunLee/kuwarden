# 2026-08-08 · 02 — Rename to KuWarden, delivery integration models, sandbox contract, tooling

**Participants:** K'Smart · Claude (Opus 5)
**Starting state:** 3 ADRs, 5 diagrams, no code, everything uncommitted.
**Ending state:** 5 ADRs, project renamed, transcript logging live, coding conventions written. Still no code. Still uncommitted.

---

## Context

Session 01 left four amendments pending and a name collision unresolved. This session closed
both, added two ADRs that emerged from questions, and set up the working conventions that
constrain how this project gets written from here.

---

## What happened

### 1. Real-time transcript logging

A `Stop` hook now mirrors the full session transcript to `log/raw/` after every turn
(`.claude/hooks/save-transcript.mjs`, registered in `.claude/settings.json`). `log/raw/` is
gitignored — the curated entries in `log/*.md` are the versioned record.

Stated plainly at the time and worth repeating: **Claude cannot guarantee "real-time" on its
own.** It can only write during its own turns, and a log that depends on it remembering will
rot. Harness-level enforcement was the only honest answer.

### 2. The control point problem — ADR 0004

K'Smart raised the sharpest architectural question of the project so far:

> "If the backend is GitHub, pushing code triggers Actions automatically — at that point it
> has basically escaped KuWarden's control."

Correct, and worse than stated. Three consequences, all breaking the design as written: the
gate was in the wrong place (if merge triggers deploy, merge *is* the deployment decision);
the credential claim was false (the CI platform holds them, not us); and a double-deploy race
existed.

A fourth was found while writing it up: **the Coder can write `.github/workflows/`**, because
CI definitions live inside the branch it has write access to. That is a direct path from
"agent produces a diff" to "arbitrary code runs with CI credentials", reachable by prompt
injection through ticket content. Closed unconditionally with `protected_paths`.

Resolved into three declared integration models (`kuflow_deploys` / `gated_merge` /
`gated_deployment`), with the general principle:

> Place the gate at the last point where KuWarden can still refuse. Where that is depends on
> who performs the deployment.

Plus the honesty rule — `control_mode` distinguishes `authorized` from `observed`, never
inferred. For an evidence product, overstating what we gated is worse than any missing
feature.

### 3. Sandbox contract — ADR 0005

Written up after K'Smart pointed out that listing the sandbox as "owed later" contradicted the
inner-loop argument. Contract fixed, implementation deliberately minimal, five security
properties non-negotiable from the first commit.

### 4. ADR 0002 amendments

Three, all from questions asked rather than review:

- **Two-stage tiering** — the facts tiering depends on do not exist at intake.
- **Coupling, not knowability** — see Corrections.
- **Revisit triggers rewritten** to match.

### 5. Conventions and knowledge base

`CLAUDE.md` (12 invariants as a checklist, the determinism boundary, conventions),
`docs/GLOSSARY.md` (fixed vocabulary + banned terms), `NON_GOALS.md`, `README.md`.

### 6. Rename: KuFlow → KuWarden

K'Smart proposed **KuAgent**. Argued against it and the argument was accepted — not primarily
for the collision ([kagent.dev](https://kagent.dev/), a CNCF Sandbox Kubernetes agent
framework, is phonetically identical) but because **it contradicts the positioning**. The
first line of `NON_GOALS.md` is "we do not build a better coding agent"; the product is the
governed control plane *above* agents. It would also have collided with our own glossary,
where "agent node" means something specific.

**KuWarden** chosen: *warden* is both one who guards and one who keeps the records — the two
halves of the product.

Executed: `gh repo rename`, remote URL updated, 26 files rewritten, all five diagrams
re-rendered. `kuflow.com` references deliberately preserved — that is the other company.

---

## Decisions

| Decision | Record |
|---|---|
| Three delivery integration models; the control point is "the last point we can refuse" | [ADR 0004](../docs/adr/0004-delivery-integration-models.md) |
| `protected_paths` — agents may never write CI/CD definitions, IaC, or policy files | [ADR 0004](../docs/adr/0004-delivery-integration-models.md) |
| `control_mode` — `authorized` vs `observed`, never inferred | [ADR 0004](../docs/adr/0004-delivery-integration-models.md) |
| Sandbox contract; workspace spans repositories; five security properties from day one | [ADR 0005](../docs/adr/0005-sandbox-contract.md) |
| Risk tiering is two-stage: provisional at intake, final from the diff | [ADR 0002](../docs/adr/0002-flow-topology.md) |
| Fan-out criterion is **contract coupling**, not decomposability | [ADR 0002](../docs/adr/0002-flow-topology.md) |
| Backend stack: Python 3.12 + `temporalio` + FastAPI + PostgreSQL | this entry |
| Renamed KuFlow → KuWarden | this entry |

---

## Corrections

**`roles_sha` was a badly chosen name.** K'Smart: *"SHA isn't a hash algorithm?"* — it is, and
in git it is shorthand for the commit identifier, but a field named for the hash function
rather than the thing it identifies invites exactly that confusion. Renamed to `roles_commit`
across 7 files. `log/` deliberately untouched: session 01 said `roles_sha` because that was
true at the time, and this directory is append-only.

**The fan-out criterion in session 01 was wrong, and K'Smart's version was better.** ADR 0002
originally used "is the decomposition statically derivable?". K'Smart argued that
multi-service, multi-module changes should stay with a single agent for consistency unless
they are genuinely batch-shaped. That is right, and it exposes that derivability was the wrong
variable — API + client + schema *is* derivable from the service catalogue and is still
exactly the change that must not be split. The real test is **whether the sub-units share a
contract**. Recorded, along with the corollary that authoring unifies while delivery sequences.

**Proposed two names without checking availability first.** Signet and Warrant both turned out
to be taken — Warrant badly so ([warrant.dev](https://warrant.dev/), an authorization platform
acquired by WorkOS, i.e. squarely in our own domain). Having just found the KuFlow collision by
searching, not searching before recommending was careless.

---

## Open

| Item | Note |
|---|---|
| **Nothing committed** | 35+ files, still on `main`, still uncommitted. Growing risk. |
| Local directory | Still `C:\repos\kuflow`. Cannot be renamed from inside an active session. |
| GitHub repo description | Still "An AI Agentic flow engine" — now contradicts the positioning. Not changed without explicit sign-off. |
| `THREAT_MODEL.md` | Two primary threats now identified: ticket prompt injection, workflow-definition write escalation. |
| `EVALUATION.md` | Still the highest-value remaining document. |
| `roles.yaml` schema + constraint evaluator | MVP version agreed at ~15 lines, no evaluator. |
| MVP defaults proposed, not confirmed | GitHub first · integration model A · static web page as the target app · a separate throwaway target repo |

---

## Artefacts

**Created:** `.claude/settings.json`, `.claude/hooks/save-transcript.mjs`, `.gitignore`,
`CLAUDE.md`, `README.md`, `NON_GOALS.md`, `docs/GLOSSARY.md`,
`docs/adr/0004-delivery-integration-models.md`, `docs/adr/0005-sandbox-contract.md`,
`log/README.md`, this entry.

**Modified:** `ARCHITECTURE.md` (§7 delivery models, `control_mode`, threat table),
`docs/adr/0002-flow-topology.md` (three amendments), `docs/adr/0003-*` (`roles_commit`),
`docs/adr/README.md`, `docs/reference/roles.example.yaml` (`protected_paths` + constraint),
all five diagrams (`.mmd`, `.svg`, `.png`), plus the rename across 26 files.

**Environment:** podman 5.4.1 present, machine started — Temporal will run as a container, so
no standalone binary download is needed. `uv`, `node 25`, `python 3.12`, `gh` (authenticated)
all available. No docker.
