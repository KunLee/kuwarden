/**
 * One run's whole chain, laid out and opened in a dialog.
 *
 * **Why a layout library here, when `FlowGraph.tsx` argues against one.** That argument still
 * holds for what it covers: the *topology* is fixed — ADR 0002 rejected a configurable flow
 * builder — so drawing thirteen boxes by hand produces a better picture than any algorithm.
 *
 * This graph is a different thing. It is the sequence a run **actually executed**, and that
 * varies: one run pushes once, another pushes four times through the `Coder ⇄ Build & Test`
 * cycle; one is rejected at the verifiers, another reaches the gate and merges. Hand-placed
 * coordinates cannot express a chain whose length is a property of the run, so `dagre` places
 * the layers and this file draws them. Layout only — a hundred lines of layered-DAG maths, not
 * a rendering framework, and the SVG below is ours.
 *
 * **Every node is an event that happened.** Nothing here is inferred: each box is a
 * `node_started` / `node_completed` pair from the audit trail, each edge is the order the
 * sequence numbers already record, and the detail panel shows that node's own notes. ADR 0012
 * §1 — the graph is recorded, never derived.
 */

import * as Dialog from "@radix-ui/react-dialog";
import dagre from "dagre";
import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { Execution, readNotes } from "./FlowGraph";
import type { Notes, RunEvent } from "../types";

/** Enough room for the longest node id at 12px, plus the status dot. */
const NODE_W = 168;
const NODE_H = 46;

type Step = {
  id: string;
  label: string;
  /** The `node_completed` payload, when the node finished. */
  notes: Notes | null;
  seq: number;
  at: string;
  state: "ok" | "failed" | "running";
  /** One-line consequence read off the flow's own events, not from the node's prose. */
  outcome?: string;
  /**
   * What `node_failed` recorded. A failed node completes no notes, so without this the panel
   * said "no notes recorded" over an event whose payload held the error the whole time.
   */
  failure?: { error: string; message: string };
};

type Chain = { steps: Step[]; edges: [string, string][] };

/**
 * Turn the event stream into the chain.
 *
 * Three shapes, and getting them apart is the whole job:
 *
 * **Repeats are separate boxes.** A node appears once per execution, so a Coder that ran twice
 * is two boxes rather than one with a counter — the second attempt is the fact worth seeing,
 * and a counter hides it.
 *
 * **The verifiers fan out.** They have `verifier_verdict` events and no `node_started` pair, so
 * they are synthesised: four nodes between Build & Test and the gate, edged in parallel because
 * that is how they ran. This is the part a hand-placed drawing cannot do and the reason a
 * layout library is here at all.
 *
 * **Flow-level events attach to what they are about.** `branch_pushed` is Push's outcome and
 * `build_test_verdict` is Build & Test's; `gate_reached` and `gate_passed` are the gate's own,
 * not a decoration on whatever node happened to run last. An earlier version attached
 * everything to the previous node and produced one Build & Test box reading
 * "exit 0 - ci; 3 of 4 passed; tier medium, 1 needed; approved" — four different facts wearing
 * one label.
 */
function chain(events: RunEvent[]): Chain {
  const steps: Step[] = [];
  const edges: [string, string][] = [];
  const open = new Map<string, number>();
  let spine: string | null = null;
  let verifiers: string[] = [];
  let gate: string | null = null;

  const add = (id: string, label: string, seq: number, at: string, state: Step["state"]) => {
    steps.push({ id, label, notes: null, seq, at, state });
    return id;
  };
  const say = (id: string | null, said: string) => {
    const at = steps.findIndex((s) => s.id === id);
    if (at >= 0)
      steps[at] = {
        ...steps[at],
        outcome: steps[at].outcome ? `${steps[at].outcome}; ${said}` : said,
      };
  };

  for (const event of events) {
    const p = event.payload as Record<string, unknown>;
    const node = event.node_id ?? "";

    if (event.kind === "node_started" && node && !node.startsWith("verifier.")) {
      const id = `${node}#${event.seq}`;
      add(id, node, event.seq, event.occurred_at, "running");
      open.set(node, steps.length - 1);
      // The spine is the sequence. After the fan-out the gate takes over as the join, so a
      // node running later attaches to the gate rather than jumping back over the verifiers.
      if (spine) edges.push([spine, id]);
      spine = id;
      continue;
    }
    if (event.kind === "node_completed" && node && open.has(node)) {
      const at = open.get(node)!;
      steps[at] = {
        ...steps[at],
        notes: readNotes(event),
        seq: event.seq,
        at: event.occurred_at,
        state: "ok",
      };
      open.delete(node);
      continue;
    }
    if (event.kind === "node_failed" && node && open.has(node)) {
      const at = open.get(node)!;
      steps[at] = {
        ...steps[at],
        state: "failed",
        seq: event.seq,
        at: event.occurred_at,
        // The flow reduces the exception to these two before recording it, and they are the
        // whole reason the event exists — see `_failure` in `delivery.py`.
        failure: {
          error: String(p.error ?? "error"),
          message: String(p.message ?? ""),
        },
      };
      steps[at] = { ...steps[at], outcome: steps[at].failure!.error };
      open.delete(node);
      continue;
    }

    if (event.kind === "verifier_verdict" && node) {
      const id = `${node}#${event.seq}`;
      const notes = readNotes(event);
      const blocked = (notes?.summary ?? "").includes("blocks");
      add(id, node.replace("verifier.", ""), event.seq, event.occurred_at,
          blocked ? "failed" : "ok");
      steps[steps.length - 1] = { ...steps[steps.length - 1], notes };
      if (spine) edges.push([spine, id]);
      verifiers.push(id);
      continue;
    }

    if (event.kind === "gate_reached") {
      gate = add(`gate#${event.seq}`, "gate", event.seq, event.occurred_at, "running");
      // Joined from every verifier that ran, or straight from the spine when none did.
      if (verifiers.length > 0) verifiers.forEach((v) => edges.push([v, gate!]));
      else if (spine) edges.push([spine, gate]);
      say(gate, `tier ${String(p.tier)}, ${String(p.needed)} needed`);
      spine = gate;
      verifiers = [];
      continue;
    }

    if (event.kind === "branch_pushed") say(spine, `pushed ${String(p.commit ?? "").slice(0, 8)}`);
    else if (event.kind === "build_test_verdict")
      say(spine, `exit ${String(p.exit_code)} · ${String(p.source)}`);
    else if (event.kind === "verifiers_completed") {
      // Only when nothing fanned out. With the four verifier boxes on screen, each already
      // showing its own verdict, "3 of 4 passed" is both redundant and attached to the wrong
      // node — it is the fan-out's result, not Build & Test's, and that is where it landed.
      if (verifiers.length === 0) say(spine, `${String(p.passed)} of ${String(p.of)} passed`);
    } else if (event.kind === "gate_passed") {
      say(gate, p.auto ? "auto-approved" : "approved");
      const at = steps.findIndex((s) => s.id === gate);
      if (at >= 0) steps[at] = { ...steps[at], state: "ok" };
    } else if (event.kind === "aborting") {
      // The verifiers that falsified the change are already red, so saying "rejected" again on
      // whatever ran last would put the fan-out's verdict on a node that passed.
      const falsified = new Set((p.falsified_by as string[] | undefined) ?? []);
      if (falsified.size === 0 && spine) {
        say(spine, "rejected");
        const at = steps.findIndex((s) => s.id === spine);
        if (at >= 0) steps[at] = { ...steps[at], state: "failed" };
      }
    } else if (event.kind === "external_effect") {
      say(spine, String(p.effect ?? "effect"));
    }
  }
  return { steps, edges };
}

const TONE: Record<Step["state"], string> = {
  ok: "fill-emerald-500",
  failed: "fill-red-500",
  running: "fill-blue-500",
};

export function RunChain({ events, runId }: { events: RunEvent[]; runId: string }) {
  const [selected, setSelected] = useState<string | null>(null);
  const [traces, setTraces] = useState<Record<string, string> | null>(null);

  // Fetched once, and only if something actually failed. The stack trace lives in Temporal
  // rather than in the audit trail — deliberately, since a trace in an append-only table
  // cannot be removed later — so it costs a separate call and is not worth making for a run
  // where every node succeeded.
  const failed = useMemo(() => events.some((e) => e.kind === "node_failed"), [events]);
  useEffect(() => {
    if (!failed || traces !== null) return;
    let alive = true;
    void api
      .runDiagnostics(runId)
      .then((rows) => {
        if (!alive) return;
        const byNode: Record<string, string> = {};
        for (const row of rows) if (row.stack_trace) byNode[row.node_id] = row.stack_trace;
        setTraces(byNode);
      })
      .catch(() => alive && setTraces({}));
    return () => {
      alive = false;
    };
  }, [failed, runId, traces]);
  const { steps, edges } = useMemo(() => chain(events), [events]);

  const laid = useMemo(() => {
    const g = new dagre.graphlib.Graph();
    // Left to right: a delivery run is a sequence, and reading it as one is the point.
    // `ranksep` is generous because the boxes carry a second line of outcome text.
    g.setGraph({ rankdir: "LR", nodesep: 20, ranksep: 56, marginx: 12, marginy: 12 });
    g.setDefaultEdgeLabel(() => ({}));
    steps.forEach((s) => g.setNode(s.id, { width: NODE_W, height: NODE_H }));
    edges.forEach(([from, to]) => g.setEdge(from, to));
    dagre.layout(g);
    return {
      graph: g.graph(),
      nodes: steps.map((s) => ({ step: s, pos: g.node(s.id) })),
      lines: edges.map(([from, to]) => g.edge(from, to)),
    };
  }, [steps, edges]);

  if (steps.length === 0)
    return <p className="text-[13px] text-muted">This run recorded no nodes.</p>;

  const width = Math.max(laid.graph.width ?? 0, 320);
  const height = Math.max(laid.graph.height ?? 0, 120);
  const open = steps.find((s) => s.id === selected) ?? null;

  return (
    <div className="grid gap-4">
      {/* Wide content scrolls inside its own box; the dialog never scrolls sideways. */}
      <div className="overflow-x-auto rounded-xl border border-line bg-surface p-2">
        <svg width={width} height={height} role="img" aria-label="What this run executed, in order">
          <defs>
            <marker id="chain-arrow" viewBox="0 0 8 8" refX="7" refY="4"
                    markerWidth="7" markerHeight="7" orient="auto">
              <path d="M0 0 L8 4 L0 8 z" className="fill-current opacity-30" />
            </marker>
          </defs>

          {laid.lines.map((e, i) =>
            !e?.points ? null : (
              <polyline
                key={i}
                points={e.points.map((p: { x: number; y: number }) => `${p.x},${p.y}`).join(" ")}
                className="fill-none stroke-current opacity-25"
                strokeWidth={1.4}
                markerEnd="url(#chain-arrow)"
              />
            ),
          )}

          {laid.nodes.map(({ step, pos }) =>
            !pos ? null : (
              <g
                key={step.id}
                transform={`translate(${pos.x - NODE_W / 2}, ${pos.y - NODE_H / 2})`}
                onClick={() => setSelected(step.id)}
                className="cursor-pointer"
              >
                <rect
                  width={NODE_W} height={NODE_H} rx={10}
                  className={`stroke-line ${
                    selected === step.id ? "fill-accent/10 stroke-accent" : "fill-surface"
                  }`}
                  strokeWidth={selected === step.id ? 1.8 : 1}
                />
                <circle cx={15} cy={NODE_H / 2} r={4} className={TONE[step.state]}>
                  {step.state === "running" && (
                    <animate attributeName="opacity" values="1;0.3;1" dur="1.4s"
                             repeatCount="indefinite" />
                  )}
                </circle>
                <text x={28} y={step.outcome ? 20 : 27}
                      className="fill-current text-[12px] font-medium">
                  {step.label}
                </text>
                {step.outcome && (
                  <text x={28} y={34} className="fill-current text-[10.5px] opacity-55">
                    {step.outcome.length > 24 ? `${step.outcome.slice(0, 23)}…` : step.outcome}
                  </text>
                )}
              </g>
            ),
          )}
        </svg>
      </div>

      {open ? (
        open.failure ? (
          <div className="grid gap-3 rounded-xl border border-red-500/25 bg-red-500/5 p-4">
            <div>
              <p className="text-[13px] font-semibold text-red-800 dark:text-red-300">
                {open.label} failed — {open.failure.error}
              </p>
              {open.failure.message && (
                <p className="mt-1 text-[13px] leading-relaxed text-red-900/85 dark:text-red-200/85">
                  {open.failure.message}
                </p>
              )}
            </div>
            {/* The trace, when Temporal still has it. A run whose history has aged out is a
                real case and reads as an absence rather than as an error. */}
            {traces === null ? (
              <p className="text-[12px] text-muted">Reading the stack trace…</p>
            ) : traces[open.label] ? (
              <details>
                <summary className="cursor-pointer text-[12px] text-muted">Stack trace</summary>
                <pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap rounded-lg bg-surface p-3 text-[11.5px] leading-relaxed">
                  {traces[open.label]}
                </pre>
              </details>
            ) : (
              <p className="text-[12px] text-muted">
                No stack trace available — Temporal keeps these outside the audit trail, and
                this run&rsquo;s history may have aged out.
              </p>
            )}
          </div>
        ) : open.notes ? (
          <Execution notes={open.notes} seq={open.seq} at={open.at} />
        ) : (
          <p className="text-[13px] text-muted">
            <strong className="font-medium">{open.label}</strong> — no notes recorded.{" "}
            {open.state === "running"
              ? "It was still executing when this run was last read."
              : "Runs recorded before notes existed have none."}
          </p>
        )
      ) : (
        <p className="text-[13px] text-muted">
          Click a step to read what it did — the same record the audit trail holds, nothing
          summarised.
        </p>
      )}
    </div>
  );
}

/** The button and the dialog it opens. Kept here so a page needs one import. */
export function RunChainButton({ events, runId }: { events: RunEvent[]; runId: string }) {
  return (
    <Dialog.Root>
      <Dialog.Trigger className="inline-flex items-center gap-2 rounded-lg border border-line px-3 py-1.5 text-[13px] font-medium transition hover:bg-surface">
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor"
             strokeWidth="1.6" aria-hidden="true">
          <circle cx="3" cy="8" r="2" />
          <circle cx="13" cy="4" r="2" />
          <circle cx="13" cy="12" r="2" />
          <path d="M5 8h3M8 8l3-3M8 8l3 4" />
        </svg>
        Full chain
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/40 backdrop-blur-[2px]" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 max-h-[88vh] w-[min(1100px,94vw)] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-2xl border border-line bg-bg p-6 shadow-2xl">
          <div className="mb-4 flex items-start justify-between gap-4">
            <div>
              <Dialog.Title className="text-[15px] font-semibold">
                What this run executed
              </Dialog.Title>
              <Dialog.Description className="mt-1 text-[13px] text-muted">
                Every box is one execution of one node, in the order the audit trail records.
                A node that ran twice appears twice — the second attempt is the fact worth
                seeing, and a counter would hide it.
              </Dialog.Description>
            </div>
            <Dialog.Close
              aria-label="Close"
              className="-mr-1 -mt-1 shrink-0 rounded-md p-1.5 text-muted transition hover:bg-surface"
            >
              <svg width="15" height="15" viewBox="0 0 14 14" fill="none" stroke="currentColor"
                   strokeWidth="1.7" strokeLinecap="round" aria-hidden="true">
                <path d="M3.5 3.5l7 7M10.5 3.5l-7 7" />
              </svg>
            </Dialog.Close>
          </div>
          <RunChain events={events} runId={runId} />
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
