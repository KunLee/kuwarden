/**
 * The run's topology as a BPMN-shaped diagram: five phases, each node's state read off the
 * audit trail, and each node's full history plus stack traces one click away.
 *
 * Hand-drawn rather than laid out by a graph library, deliberately. The topology is **fixed**
 * — ADR 0002 rejected a configurable flow builder outright — so there is no arbitrary graph
 * to lay out. A general-purpose library would add several hundred kilobytes and a security
 * review to every air-gapped deployment (CLAUDE.md: do not add a dependency without saying
 * why) in exchange for an automatic layout that cannot express the two things this diagram
 * most needs to: that Coder → Push ⇄ Build & Test is a bounded cycle, and that the gate is a
 * gateway rather than a task.
 *
 * **Phases, and the verifiers as one box.** An earlier version drew thirteen boxes in a
 * wrapping row and read as a wall. The four verifiers are identical in kind and always run
 * together, so they are one node showing ×4 — three boxes removed and nothing lost, because
 * "which of the four" is a question for the popup, not for the overview.
 *
 * Two visual dimensions, chosen so neither drowns the other:
 *
 * **State is loud** — border and fill. Where is it, what broke.
 *
 * **Node class is quiet** — a left colour bar and an icon. It carries what this project is
 * about: which nodes contain a model. Invariant 1 says the Flow Engine contains none, and a
 * diagram where `deterministic` and `generative` look identical hides the single distinction
 * the architecture is built on.
 */

import { useEffect, useState } from "react";

import { api } from "../api";
import type { RunEvent } from "../types";

/**
 * `interrupted` exists because runs end badly: the node started, the run is over, and no
 * outcome was recorded. Rendering that as "running" puts a live spinner on a finished run.
 */
type NodeState = "pending" | "running" | "ok" | "failed" | "interrupted";

/** Mirrors `engine.state.NodeClass`. `gateway` is BPMN's, not the engine's — the approval
 *  gate is flow logic rather than a node, and drawing it as a task would misstate that. */
type NodeClass = "deterministic" | "generative" | "verifier" | "gateway" | "event";

interface Spec {
  /** One id, or several when a box stands for a group — the verifiers. */
  ids: string[];
  label: string;
  cls: NodeClass;
  blurb: string;
}

interface Phase {
  name: string;
  note?: string;
  nodes: Spec[];
}

const PHASES: Phase[] = [
  {
    name: "Intake",
    nodes: [
      {
        ids: ["__start"],
        label: "Ticket",
        cls: "event",
        blurb: "A work item admitted by the trigger's rules — label, state, story points.",
      },
      {
        ids: ["triage"],
        label: "Triage & Risk Router",
        cls: "deterministic",
        blurb:
          "Fetches the ticket and applies admission control. Assigns a provisional risk tier, " +
          "which may later be raised and never lowered (invariant 5).",
      },
      {
        ids: ["planner"],
        label: "Planner",
        cls: "generative",
        blurb: "Ticket plus codebase to a structured change plan. The first model call.",
      },
    ],
  },
  {
    name: "Build",
    note: "bounded cycle · up to 4 attempts",
    nodes: [
      {
        ids: ["coder"],
        label: "Coder",
        cls: "generative",
        blurb:
          "Writes the change inside an ephemeral sandbox with no network and no credentials. " +
          "The diff comes from git afterwards, never from the model's account of it.",
      },
      {
        ids: ["push"],
        label: "Push",
        cls: "deterministic",
        blurb:
          "Writes the branch so CI has something to run on (ADR 0007). Denies protected " +
          "paths before anything reaches origin.",
      },
      {
        ids: ["build_test"],
        label: "Build & Test",
        cls: "deterministic",
        blurb:
          "Runs the suite in the sandbox, then reads the project's own pipeline for the " +
          "pushed commit when one is configured. The exit code is the verdict.",
      },
    ],
  },
  {
    name: "Verify",
    note: "fan-out · fresh context each",
    nodes: [
      {
        // `__verifiers` first: the fan-out is bracketed by `verifiers_started` /
        // `verifiers_completed`, which carry no `node_id` because `_verify` runs the four
        // with `record=False`. Without it the box read "not reached" on a run where they
        // plainly ran — the picture contradicting the evidence it claims to be derived from.
        //
        // The four individual ids stay, and contribute nothing while they emit nothing. A
        // verifier that *fails* does emit `node_failed`, and its failure then wins the
        // combined state.
        ids: [
          "__verifiers",
          "verifier.correctness",
          "verifier.security",
          "verifier.test_evidence",
          "verifier.regression_risk",
        ],
        label: "Verifiers ×4",
        cls: "verifier",
        blurb:
          "Correctness, security, test evidence, regression risk — each in a context that has " +
          "never seen the Coder's reasoning. Any one of them may block. Currently stubs: they " +
          "pass unconditionally, so nothing has reviewed the diff.",
      },
    ],
  },
  {
    name: "Decide",
    nodes: [
      {
        ids: ["__gate"],
        label: "Approval gate",
        cls: "gateway",
        blurb:
          "Depth set by risk tier: low needs nobody, medium one approver, high two. The run " +
          "suspends without holding a resource open, and may wait days.",
      },
    ],
  },
  {
    name: "Deliver",
    nodes: [
      {
        ids: ["release"],
        label: "Release",
        cls: "deterministic",
        blurb:
          "Opens the pull request — a request addressed to a human, made only after the " +
          "verifiers and the gate have both passed.",
      },
      {
        ids: ["reporter"],
        label: "Reporter",
        cls: "deterministic",
        blurb: "Posts the outcome and the evidence back to the ticket. Runs on every path.",
      },
      { ids: ["__end"], label: "Done", cls: "event", blurb: "" },
    ],
  },
];

/** Only Abort. Reporter used to live here, which rendered an "on failure" panel on every
 *  successful run — Reporter runs on every path, so its presence proves nothing. */
const COMPENSATION: Spec[] = [
  {
    ids: ["compensate"],
    label: "Abort / Rollback",
    cls: "deterministic",
    blurb:
      "Driven from outside the thing that failed, because a crashed process cannot clean up " +
      "after itself. Runs even if the original worker died.",
  },
];

const CLASS_BAR: Record<NodeClass, string> = {
  deterministic: "bg-slate-400",
  generative: "bg-violet-500",
  verifier: "bg-cyan-500",
  gateway: "bg-amber-500",
  event: "bg-line",
};

const CLASS_LABEL: Record<NodeClass, string> = {
  deterministic: "deterministic — no model",
  generative: "generative — contains a model",
  verifier: "verifier — a model, in fresh context",
  gateway: "gateway — a human decides",
  event: "start / end",
};

const STATE_BOX: Record<NodeState, string> = {
  pending: "border-line bg-transparent",
  running: "border-blue-500/50 bg-blue-500/8",
  ok: "border-emerald-500/40 bg-emerald-500/6",
  failed: "border-red-500/50 bg-red-500/8",
  interrupted: "border-amber-500/50 bg-amber-500/8",
};

const STATE_PIP: Record<NodeState, string> = {
  pending: "ring-1 ring-line",
  running: "bg-blue-500 animate-pulse",
  ok: "bg-emerald-500",
  failed: "bg-red-500",
  interrupted: "bg-amber-500",
};

const STATE_WORD: Record<NodeState, string> = {
  pending: "not reached",
  running: "running",
  ok: "ok",
  failed: "failed",
  interrupted: "interrupted",
};

/** Inline paths. An icon set would be another dependency for five glyphs. */
function Icon({ cls }: { cls: NodeClass }) {
  const base = { width: 13, height: 13, viewBox: "0 0 16 16", "aria-hidden": true } as const;
  if (cls === "generative")
    return (
      <svg {...base} fill="currentColor">
        <path d="M8 1l1.6 4.4L14 7l-4.4 1.6L8 13l-1.6-4.4L2 7l4.4-1.6L8 1z" />
      </svg>
    );
  if (cls === "verifier")
    return (
      <svg {...base} fill="none" stroke="currentColor" strokeWidth="1.7">
        <circle cx="7" cy="7" r="4.5" />
        <path d="M10.5 10.5L14.5 14.5" strokeLinecap="round" />
      </svg>
    );
  if (cls === "gateway")
    return (
      <svg {...base} fill="none" stroke="currentColor" strokeWidth="1.7">
        <path d="M8 1.5l6.5 6.5L8 14.5 1.5 8z" />
      </svg>
    );
  if (cls === "event")
    return (
      <svg {...base} fill="none" stroke="currentColor" strokeWidth="1.7">
        <circle cx="8" cy="8" r="6" />
      </svg>
    );
  return (
    <svg {...base} fill="none" stroke="currentColor" strokeWidth="1.7">
      <rect x="2" y="3" width="12" height="10" rx="2" />
      <path d="M5 7h6M5 10h4" strokeLinecap="round" />
    </svg>
  );
}

interface NodeView {
  state: NodeState;
  attempts: number;
  ms: number | null;
  detail: string | null;
  events: RunEvent[];
}

const EMPTY: NodeView = { state: "pending", attempts: 0, ms: null, detail: null, events: [] };

function duration(ms: number): string {
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
}

/**
 * Fold the event stream into one view per node.
 *
 * A node appears more than once — the Coder loop runs up to four times — so starts are
 * counted rather than assumed unique, and the *last* outcome wins. Showing the first would
 * report a run that recovered on attempt 3 as failed.
 */
function derive(events: RunEvent[], runFinished: boolean): Map<string, NodeView> {
  const views = new Map<string, NodeView>();
  const startedAt = new Map<string, number>();
  const at = (event: RunEvent) => new Date(event.occurred_at).getTime();

  const view = (id: string): NodeView => {
    const found = views.get(id);
    if (found) return found;
    const created: NodeView = { ...EMPTY, events: [] };
    views.set(id, created);
    return created;
  };

  for (const event of events) {
    // The gate is flow logic, not a node, so its events carry no `node_id`. Mapped onto the
    // gateway shape — a diagram missing the approval step is missing the control point.
    if (event.kind.startsWith("verifiers_")) {
      const fan = view("__verifiers");
      fan.events.push(event);
      fan.state = event.kind === "verifiers_started" ? "running" : "ok";
      continue;
    }
    if (event.kind.startsWith("gate_")) {
      const gate = view("__gate");
      gate.events.push(event);
      gate.state =
        event.kind === "gate_reached" ? "running" : event.kind === "gate_rejected" ? "failed" : "ok";
      if (event.kind === "gate_rejected") gate.detail = "rejected by an approver";
      continue;
    }
    if (!event.node_id) continue;

    const current = view(event.node_id);
    current.events.push(event);
    const began = startedAt.get(event.node_id);

    if (event.kind === "node_started") {
      current.attempts += 1;
      current.state = "running";
      startedAt.set(event.node_id, at(event));
    } else if (event.kind === "node_completed") {
      current.state = "ok";
      current.detail = null;
      if (began !== undefined) current.ms = at(event) - began;
    } else if (event.kind === "node_failed") {
      current.state = "failed";
      const error = String(event.payload.error ?? "error");
      const message = String(event.payload.message ?? "");
      current.detail = message ? `${error} — ${message}` : error;
      if (began !== undefined) current.ms = at(event) - began;
    }
  }

  if (runFinished) {
    for (const current of views.values()) {
      if (current.state === "running") {
        current.state = "interrupted";
        current.detail ??= "started, and the run ended without recording an outcome";
      }
    }
  }

  if (views.size > 0) views.set("__start", { ...EMPTY, state: "ok" });
  if (views.get("release")?.state === "ok") views.set("__end", { ...EMPTY, state: "ok" });
  return views;
}

/** Collapse several nodes into one box. The worst state wins — a green box over one failed
 *  verifier would be a diagram that lies about the thing it exists to report. */
function combine(views: Map<string, NodeView>, ids: string[]): NodeView {
  const parts = ids.map((id) => views.get(id)).filter((v): v is NodeView => v !== undefined);
  if (parts.length === 0) return EMPTY;
  const order: NodeState[] = ["failed", "interrupted", "running", "pending", "ok"];
  const state = order.find((s) => parts.some((p) => p.state === s)) ?? "ok";
  const durations = parts.map((p) => p.ms).filter((ms): ms is number => ms !== null);
  return {
    state,
    attempts: Math.max(...parts.map((p) => p.attempts)),
    ms: durations.length ? Math.max(...durations) : null,
    detail: parts.find((p) => p.detail)?.detail ?? null,
    events: parts.flatMap((p) => p.events).sort((a, b) => a.seq - b.seq),
  };
}

function Node({
  spec,
  view,
  last,
  onOpen,
}: {
  spec: Spec;
  view: NodeView;
  last: boolean;
  onOpen: () => void;
}) {
  return (
    <div className="flex items-center">
      <button
        type="button"
        onClick={onOpen}
        title={CLASS_LABEL[spec.cls]}
        className={`relative flex items-center gap-2 overflow-hidden border py-2 pl-3.5 pr-3 text-left transition hover:shadow-sm ${
          spec.cls === "event" ? "rounded-full" : "rounded-lg"
        } ${STATE_BOX[view.state]}`}
      >
        <span className={`absolute inset-y-0 left-0 w-1 ${CLASS_BAR[spec.cls]}`} aria-hidden />
        <span className="text-muted">
          <Icon cls={spec.cls} />
        </span>
        <span>
          {/* No truncation and no fixed width. A box that clips "Triage & Risk Router" to
              "Triage & Risk ..." saves nothing a reader wanted saved. */}
          <span className="block whitespace-nowrap text-[12px] font-medium">{spec.label}</span>
          <span className="mt-0.5 block whitespace-nowrap text-[10px] text-muted tabular-nums">
            {STATE_WORD[view.state]}
            {view.ms !== null && ` · ${duration(view.ms)}`}
            {view.attempts > 1 && ` · ${view.attempts}×`}
          </span>
        </span>
        <span className={`ml-1 size-1.5 shrink-0 rounded-full ${STATE_PIP[view.state]}`} />
      </button>
      {/* Owned by the node, not placed between nodes: a separate arrow item lands at the
          start of a line when the row wraps and points back into the margin. */}
      {!last && <span className="px-1.5 text-muted">→</span>}
    </div>
  );
}

export function FlowGraph({
  runId,
  events,
  runStatus,
}: {
  runId: string;
  events: RunEvent[];
  runStatus?: string;
}) {
  const [open, setOpen] = useState<Spec | null>(null);
  const finished = runStatus !== undefined && !["running", "suspended"].includes(runStatus);
  const views = derive(events, finished);
  const compensated = COMPENSATION.some((spec) => views.has(spec.ids[0]));

  const phase = (nodes: Spec[]) =>
    nodes.map((spec, i) => (
      <Node
        key={spec.ids[0]}
        spec={spec}
        view={combine(views, spec.ids)}
        last={i === nodes.length - 1}
        onOpen={() => setOpen(spec)}
      />
    ));

  return (
    <div className="space-y-3">
      {PHASES.map((p) => (
        <div key={p.name} className="grid gap-x-4 gap-y-1 sm:grid-cols-[7rem_1fr]">
          <div className="pt-2">
            <div className="text-[11px] font-semibold uppercase tracking-wide text-muted">
              {p.name}
            </div>
            {p.note && <div className="text-[10px] leading-tight text-muted">{p.note}</div>}
          </div>
          <div className="flex flex-wrap items-center gap-y-2">{phase(p.nodes)}</div>
        </div>
      ))}

      {compensated && (
        <div className="grid gap-x-4 gap-y-1 rounded-lg border border-amber-500/30 bg-amber-500/4 p-2 sm:grid-cols-[7rem_1fr]">
          <div className="pt-2 text-[11px] font-semibold uppercase tracking-wide text-amber-700 dark:text-amber-400">
            On failure
          </div>
          <div className="flex flex-wrap items-center gap-y-2">{phase(COMPENSATION)}</div>
        </div>
      )}

      <div className="flex flex-wrap gap-x-4 gap-y-1 pt-1 text-[11px] text-muted">
        {(["deterministic", "generative", "verifier", "gateway"] as NodeClass[]).map((cls) => (
          <span key={cls} className="flex items-center gap-1.5">
            <span className={`size-2 rounded-sm ${CLASS_BAR[cls]}`} />
            {CLASS_LABEL[cls]}
          </span>
        ))}
      </div>

      {open && (
        <NodeDetail
          spec={open}
          view={combine(views, open.ids)}
          runId={runId}
          onClose={() => setOpen(null)}
        />
      )}
    </div>
  );
}

function NodeDetail({
  spec,
  view,
  runId,
  onClose,
}: {
  spec: Spec;
  view: NodeView;
  runId: string;
  onClose: () => void;
}) {
  const [traces, setTraces] = useState<
    { node_id: string; error: string; message: string; stack_trace: string }[] | null
  >(null);
  const [unattributed, setUnattributed] = useState(false);
  const [unavailable, setUnavailable] = useState<string | null>(null);
  const stalled = view.state === "failed" || view.state === "interrupted";

  useEffect(() => {
    if (!runId) return;
    // Fetched on open rather than with the page: it reaches Temporal, and most nodes a reader
    // clicks did not fail.
    void api
      .runDiagnostics(runId)
      .then((rows) => {
        const mine = rows.filter((r) => spec.ids.includes(r.node_id));
        if (mine.length > 0) {
          setTraces(mine);
          setUnattributed(false);
          return;
        }
        // Runs recorded before activities were named carry Temporal's bare counter, which
        // cannot be attributed to a node. Showing nothing would hide a stack trace we are
        // holding — so on the node that actually stalled, show the unattributed ones and say
        // that is what they are.
        const orphans = rows.filter((r) => r.node_id === "");
        setTraces(stalled ? orphans : []);
        setUnattributed(stalled && orphans.length > 0);
      })
      .catch((e) => setUnavailable(e instanceof Error ? e.message : String(e)));
  }, [runId, spec, stalled]);

  return (
    <div className="rounded-xl border border-line bg-surface p-4 shadow-sm">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <span className={`size-2 rounded-sm ${CLASS_BAR[spec.cls]}`} />
            <span className="text-sm font-medium">{spec.label}</span>
            <span className="text-[11px] text-muted">{CLASS_LABEL[spec.cls]}</span>
          </div>
          {spec.blurb && (
            <p className="mt-1.5 max-w-3xl text-[13px] leading-relaxed text-muted">{spec.blurb}</p>
          )}
        </div>
        <button type="button" onClick={onClose} className="shrink-0 text-xs text-muted hover:text-ink">
          Close
        </button>
      </div>

      {view.detail && (
        <div className="mt-3 rounded-lg border border-red-500/25 bg-red-500/6 px-3 py-2 text-[12px] text-red-700 dark:text-red-300">
          {view.detail}
        </div>
      )}

      {view.events.length > 0 && (
        <>
          <div className="mt-4 text-[11px] font-semibold uppercase tracking-wide text-muted">
            Audit trail — the permanent record
          </div>
          <table className="mt-1 w-full text-[12px]">
            <tbody>
              {view.events.map((event) => (
                <tr key={`${event.node_id}-${event.seq}`} className="border-b border-line/60 last:border-0">
                  <td className="w-8 py-1.5 text-muted tabular-nums">{event.seq}</td>
                  <td className="w-40 py-1.5 font-medium">{event.kind}</td>
                  <td className="w-24 py-1.5 text-muted tabular-nums">
                    {new Date(event.occurred_at).toLocaleTimeString()}
                  </td>
                  <td className="mono py-1.5 text-[11px] text-muted">
                    {Object.keys(event.payload).length > 0 ? JSON.stringify(event.payload) : ""}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {/* Kept visually apart from the trail above, because they are different kinds of thing:
          one is the record, the other is diagnosis that expires with Temporal's retention. */}
      {traces && traces.length > 0 && (
        <>
          <div className="mt-4 text-[11px] font-semibold uppercase tracking-wide text-muted">
            Execution detail — from Temporal, not the record
          </div>
          {unattributed && (
            <p className="mt-1 text-[11px] text-amber-700 dark:text-amber-400">
              Recorded before activities were named, so this failure cannot be attributed to a
              specific node. Shown here because this is the node the run stalled on.
            </p>
          )}
          {traces.map((trace, i) => (
            <div key={i} className="mt-1.5 rounded-lg border border-line bg-canvas p-3">
              <div className="text-[12px] font-medium text-red-700 dark:text-red-300">
                {trace.error} — {trace.message}
              </div>
              {trace.stack_trace && (
                <pre className="mono mt-2 max-h-64 overflow-auto whitespace-pre-wrap text-[10.5px] leading-relaxed text-muted">
                  {trace.stack_trace}
                </pre>
              )}
            </div>
          ))}
        </>
      )}

      {unavailable && (
        <p className="mt-3 text-[11px] text-muted">
          Execution detail unavailable: {unavailable}
        </p>
      )}
    </div>
  );
}
