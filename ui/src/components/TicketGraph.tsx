/**
 * The evidence graph for one ticket — ADR 0012.
 *
 * **A matrix, not a node-link diagram, and that is the whole design.** Runs are columns in the
 * order they started; files are rows; a cell is "this run changed this file". The question
 * this exists to answer is *who else has been in here*, and in a matrix that answer is a row
 * with more than one dot — visible without tracing a single edge.
 *
 * A force-directed layout was rejected (ADR 0012). It earns its cost on graphs too large to
 * read by eye; at tens of nodes it produces a drifting cluster whose positions carry no
 * information, and it throws away the ordering that an audit trail always has. Time is the one
 * axis that always means something here, so time is an axis.
 *
 * No layout library and no new dependency: the coordinates below are arithmetic. The same
 * argument `FlowGraph.tsx` makes for the fixed topology, for the same air-gapped reason.
 *
 * The row that would have prevented a real incident: four runs for ticket 50 branched from one
 * base, all edited `components/Header.tsx`, none could see the others, and two were merged —
 * the second by hand into a state nothing had verified.
 */

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import type { TicketGraph as Graph } from "../types";

const CELL = 26;
const HEADER = 92;
const LABEL = 300;

/** Status colours, matching StatusBadge so one run reads the same in both places. */
const TONE: Record<string, string> = {
  succeeded: "fill-emerald-500",
  running: "fill-blue-500",
  suspended: "fill-violet-500",
  rejected: "fill-amber-500",
  failed: "fill-red-500",
  aborted: "fill-slate-400",
  terminated: "fill-stone-500",
};

export function TicketGraph({ runId }: { runId: string }) {
  const [graph, setGraph] = useState<Graph | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    api
      .runGraph(runId)
      .then((g) => alive && setGraph(g))
      .catch(() => alive && setFailed(true));
    return () => {
      alive = false;
    };
  }, [runId]);

  if (failed) return <p className="text-[13px] text-muted">The graph could not be loaded.</p>;
  if (!graph) return null;

  const runs = graph.nodes.filter((n) => n.kind === "run");
  const files = graph.nodes.filter((n) => n.kind === "file");
  // Files first that the most runs touched. A collision is the point of the view, so it goes
  // at the top rather than wherever the path sorts alphabetically.
  const touchedBy = (path: string) =>
    graph.edges.filter((e) => e.kind === "changed" && e.to === `file:${path}`);
  const rows = [...files].sort(
    (a, b) => touchedBy(b.label).length - touchedBy(a.label).length
  );

  if (runs.length === 0) return null;

  const width = LABEL + runs.length * CELL + 16;
  const height = HEADER + rows.length * CELL + 8;

  return (
    <div className="grid gap-3">
      <p className="text-[13px] text-muted">
        {runs.length} run{runs.length === 1 ? "" : "s"} for this ticket
        {rows.length > 0 && <>, touching {rows.length} file{rows.length === 1 ? "" : "s"}</>}.
        A row with more than one mark is a file two runs changed without seeing each other.
      </p>

      {/* Wide content scrolls inside its own container; the page never scrolls sideways. */}
      <div className="overflow-x-auto">
        <svg width={width} height={height} className="min-w-0" role="img"
             aria-label="Runs for this ticket and the files each changed">
          {runs.map((run, column) => {
            const x = LABEL + column * CELL + CELL / 2;
            return (
              <g key={run.id}>
                {/* Rotated, because a run id is far wider than a column. */}
                <text
                  x={x} y={HEADER - 10} transform={`rotate(-60 ${x} ${HEADER - 10})`}
                  className={`fill-current text-[10px] ${run.self ? "font-semibold" : ""}`}
                  textAnchor="start"
                >
                  {run.label}
                  {run.revision ? ` r${run.revision}` : ""}
                </text>
                <circle cx={x} cy={HEADER + 2} r={4}
                        className={TONE[run.status] ?? "fill-slate-400"} />
                {/* The run under inspection keeps its column visible behind the marks. */}
                {run.self && (
                  <rect x={x - CELL / 2} y={HEADER + 8} width={CELL}
                        height={rows.length * CELL} className="fill-current opacity-5" />
                )}
              </g>
            );
          })}

          {rows.map((file, row) => {
            const y = HEADER + 8 + row * CELL + CELL / 2;
            const marks = touchedBy(file.label);
            const shared = marks.length > 1;
            return (
              <g key={file.id}>
                <text x={0} y={y + 4}
                      className={`fill-current text-[11px] ${shared ? "font-semibold" : "opacity-70"}`}>
                  {file.label.length > 44 ? `…${file.label.slice(-43)}` : file.label}
                </text>
                {runs.map((run, column) => {
                  const edge = marks.find((e) => e.from === run.id);
                  if (!edge) return null;
                  return (
                    <circle
                      key={run.id}
                      cx={LABEL + column * CELL + CELL / 2}
                      cy={y}
                      r={shared ? 5 : 3.5}
                      className={shared ? "fill-amber-500" : "fill-current opacity-40"}
                    >
                      <title>
                        {`${run.label} changed ${file.label} (+${edge.added}/-${edge.removed})`}
                      </title>
                    </circle>
                  );
                })}
              </g>
            );
          })}
        </svg>
      </div>

      <ul className="grid gap-1 text-[12px] text-muted">
        {runs.map((run) => (
          <li key={run.id}>
            <Link to={`/runs/${run.id.replace("run:", "")}`}
                  className="underline decoration-dotted underline-offset-2">
              {run.label}
            </Link>
            {run.revision ? ` · revision r${run.revision}` : ""} · {run.status} ·{" "}
            {run.pushes.length} push{run.pushes.length === 1 ? "" : "es"}
            {/* The base every run branched from. Runs sharing a base and a file is the
                collision; showing the base is how a reader sees it coming. */}
            {run.pushes[0]?.base && ` · base ${run.pushes[0].base.slice(0, 8)}`}
          </li>
        ))}
      </ul>
    </div>
  );
}
