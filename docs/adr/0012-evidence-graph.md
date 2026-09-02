# ADR 0012 — The evidence graph is recorded, never derived

- **Status:** Accepted
- **Date:** 2026-08-31
- **Depends on:** [ADR 0003](0003-role-graph-and-traceability.md) — the audit trail is a tree and append-only
- **Constrains:** `engine/db/migrations/`, `engine/api/main.py`, `ui/src/components/`

---

## Context

A run's record is complete and unreadable. Answering *"why was this change allowed to ship"*
today means reading `flow_events` row by row, and answering *"what else touched this file"*
is not possible at all.

The pressure to fix this arrived with a specific proposal: build the graph the way a
document-understanding product builds one — extract text, have a model infer an ontology,
chunk it, and let a GraphRAG service discover entities and relationships. That is a good
design for its problem. It is the wrong design for this one, and the reason is worth writing
down because the proposal will recur every time a graph feature is discussed.

**Our edges are not discovered. They are already facts the system wrote down:**

| Edge | Where it already lives |
|---|---|
| this run's parent run | `flow_runs.parent_run_id` — a foreign key |
| this step's position in this run | `flow_events.seq` — a column, under an append-only trigger |
| this commit belongs to this run | the `kuwarden-run-id` trailer **KuWarden itself wrote** |
| this run changed these files | git's own diff, recorded in the Push node |
| this run's tier was raised for this reason | `risk_tier_final` |
| KuWarden performed this merge | `external_effect` with `control_mode = 'authorized'` |

There is no corpus, nothing to chunk, and no ontology to infer — the ontology is the schema,
declared in migrations and enforced by constraints.

## Decision

**The evidence graph is assembled from recorded facts, in PostgreSQL, and no model
participates in producing it.**

Three parts.

### 1. No model may produce an evidence edge

This follows from the rule everything else follows from — *whatever must be deterministic,
auditable, or privileged does not get to be a model* — and it is stated separately here
because the temptation arrives disguised as a feature.

An edge inferred by a model is **an agent's account of what happened**. Ask "how do you know
this run touched that file" and the answer becomes "a language model read the logs and
thought so". That answer destroys the artifact it is decorating. The same objection as
invariant 3, one level up: a verdict may not be an agent's claim, and neither may a fact
about how a change reached production.

**The line is what the output is used for, not what technique produced it.** The same
LLM-derived index that is forbidden here is perfectly legitimate for *retrieval* — see ADR
0010's repository-map alternative — because a bad retrieval yields a worse answer, while a bad
edge yields a false record. Anyone proposing AI-derived data should be asked which side of
that line it lands on, and the answer decides it.

### 2. PostgreSQL, not a graph database, and nothing hosted

The graph is small and hierarchical. A ticket has a handful of runs; a run has tens of events
and a handful of files. Recursive CTEs answer every question we have, over data that is
already relationally modelled and already protected by the constraints and triggers that make
it evidence in the first place.

A hosted service is refused outright and not on cost grounds. `NON_GOALS.md`: *"We do not
offer a hosted SaaS control plane. Self-hosting is not a deployment option, it is the
product."* The payload here would be ticket text, plans, diffs and source — the exact things
the product exists to keep inside the perimeter, and unavailable to an air-gapped deployment
by construction.

### 3. One new table, and it holds no new facts

```sql
run_files(run_id, path, change)
```

Written by the Push activity from the diff git computed. It creates no knowledge — it indexes
what the Push node already records in prose, so that the reverse question becomes answerable:
**which runs changed this file.**

That question is the one that would have prevented a real incident. Four runs for ticket 50
branched from one base, all edited `components/Header.tsx`, none could see the others, two
were merged, and the second merge had to be resolved by hand into a state nothing had
verified.

### Layout is a rendering decision, deferred

Time is the one axis that always carries meaning in an audit trail, so the first view is a
deliberate layout — ticket on the left, runs as lanes, time along the horizontal, shared files
as a joined column — rendered as SVG the way `FlowGraph.tsx` already renders the fixed
topology. Whether an automatic layout is ever needed is a question to answer against real data
on a screen, not in advance.

## Consequences

**Two endpoints carry most of the value before any pixel is drawn.** `GET
/api/runs/{id}/graph` and `GET /api/files/{path}/runs` are useful from a terminal, and they
are what makes the view replaceable rather than load-bearing.

**The graph is only as good as what is recorded.** It cannot show a fact nobody wrote down —
and the corollary bit this project twice in one week: the approval page omitted the verifier
findings, and no amount of graph would have added them. Recording comes first; the graph draws
what is there.

**`run_files` must never become authoritative.** It is an index over the Push node's record,
and the Push node's record is derived from git. If they ever disagree, git wins and the index
is wrong.

## Alternatives considered

### Zep Cloud GraphRAG, as used in a sibling project

*Rejected, twice over.* It is a hosted service, which ends the discussion by itself. And its
engine — Graphiti — is a **temporal** knowledge graph whose defining feature is that facts are
superseded and invalidated as new information arrives. That is the correct semantics for agent
memory and the exact inverse of invariant 9, which forbids ever updating an audit row.

**Revisit if** it is ever offered self-hosted *and* the project acquires a genuine
document-understanding problem. Neither condition applies to the evidence graph, and the first
alone would not be enough.

### Microsoft GraphRAG, self-hosted

*Rejected for evidence; recommended to consider elsewhere.* MIT-licensed and runs locally, so
the hosting objection does not apply — but it still infers the graph with a model, which is
§1. It is a good candidate for the repository map ADR 0011 lists as its cheapest alternative,
where the output is retrieval and non-determinism costs quality rather than truth.

### A self-hosted graph database (Neo4j, Memgraph)

*Rejected as a dependency bought ahead of the problem.* Every dependency ships into air-gapped
environments as somebody's security review, and Postgres answers today's questions.

**Revisit when** a traversal we actually need is unbounded in depth or spans enough rows that
a recursive CTE stops performing — for example tracing production incidents back through
changes to the policy version that authorised them, across the whole history of an
organisation.

### A force-directed or 3D visualisation

*Rejected for the audit view.* Force layout earns its cost on graphs large enough that
clusters cannot be seen by eye; at tens of nodes it produces a drifting cluster whose
positions carry no information, and it discards the ordering that an audit trail always has.
3D adds occlusion and does not survive a screenshot, which is how this artifact will actually
be used.

**Revisit if** a view is wanted for demonstration rather than for audit — a legitimate purpose,
and one that should then be built and labelled as a separate artifact rather than becoming the
default view. Formality outrunning evidence is the failure mode this project is least able to
afford.

## Revisit triggers

- **Recursive CTE performance**, per the graph-database alternative above.
- **A second application, or child runs.** `parent_run_id` exists and has never been
  exercised; the first fan-out will test whether the schema is the right shape.
- **Anything proposing AI-derived data.** Not a trigger to revisit this ADR so much as a
  prompt to apply §1's question: is this output retrieval, or is it evidence?
