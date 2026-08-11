/**
 * One run's audit trail.
 *
 * This is the view ADR 0003 owes: the audit trail is only evidence if a non-engineer can
 * read it. `control_mode` is rendered as its own column rather than as a footnote, because
 * the distinction between what KuWarden authorised and what it merely observed is what makes
 * the rest of the record credible.
 */

import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, ApiError } from "../api";
import { Banner, Button, Card, Empty, PageHeader } from "../components/ui";
import { useCan } from "../auth";
import { ApprovalGate } from "../components/ApprovalGate";
import { FlowGraph } from "../components/FlowGraph";
import type { Run, RunEvent } from "../types";

/** Statuses that can still produce another event. Anything else is terminal. */
const IN_FLIGHT = ["running", "suspended"];

export function RunDetail() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const canApprove = useCan("approver");
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [run, setRun] = useState<Run | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [live, setLive] = useState(false);

  /**
   * Start the same ticket again.
   *
   * A *new* run, deliberately — not a resume and not a retry of this one. The audit trail is
   * append-only, so a run that failed stays failed and keeps its record; re-running produces
   * a second record that can be compared with the first. Reusing the id would overwrite the
   * evidence of what went wrong, which is the one thing that must not happen.
   */
  async function runAgain() {
    if (!run) return;
    setBusy(true);
    setMessage(null);
    try {
      const started = await api.startRun(run.app_id, run.ticket_id);
      navigate(`/runs/${started.run_id}`);
    } catch (e) {
      setMessage(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  /**
   * Poll while the run is in flight, and stop the moment it is not.
   *
   * Chained `setTimeout` rather than `setInterval`: an interval fires on schedule regardless
   * of whether the previous request came back, so a slow response produces overlapping
   * requests that can also apply their results out of order.
   *
   * Polling rather than a stream. A websocket or SSE channel means connection state,
   * reconnection and a server-side fan-out to get right, for a page one operator has open —
   * and the audit trail is already the source of truth, so a missed frame costs nothing that
   * the next tick does not fix.
   */
  useEffect(() => {
    let alive = true;
    let timer: number | undefined;

    const poll = async () => {
      try {
        const [nextEvents, runs] = await Promise.all([
          api.listRunEvents(id),
          // The run row carries the status, and the gate only renders for a suspended run.
          // Listing and filtering keeps this to the endpoints that exist.
          api.listRuns(),
        ]);
        if (!alive) return;
        setEvents(nextEvents);
        const found = runs.find((r) => r.id === id) ?? null;
        setRun(found);
        setLive(found !== null && IN_FLIGHT.includes(found.status));
        // A finished run cannot produce another event. Continuing to ask is a request every
        // few seconds that can never return anything new.
        if (found && !IN_FLIGHT.includes(found.status)) return;
      } catch {
        // Backs off rather than stopping: a transient failure should not leave the page
        // frozen on a stale picture with no way back except a reload.
        if (alive) timer = window.setTimeout(() => void poll(), 5000);
        return;
      }
      if (alive) timer = window.setTimeout(() => void poll(), 2000);
    };

    void poll();
    return () => {
      alive = false;
      if (timer) window.clearTimeout(timer);
    };
  }, [id]);

  return (
    <div>
      <PageHeader
        title="Audit trail"
        description="Append-only. What KuWarden authorised, what it merely observed, and what represents no external effect at all."
        actions={
          <div className="flex items-center gap-3">
            {live && (
              <span className="flex items-center gap-1.5 text-[11px] text-muted">
                <span className="size-1.5 animate-pulse rounded-full bg-blue-500" />
                live
              </span>
            )}
            {run &&
          canApprove && (
            <Button variant="primary" onClick={runAgain} disabled={busy}>
              {busy ? "Starting…" : `Run ${run.ticket_id} again`}
            </Button>
          )}
          </div>
        }
      />
      {message && (
        <div className="mb-4">
          <Banner tone="error">{message}</Banner>
        </div>
      )}
      {run && (
        <div className="mb-6">
          <ApprovalGate runId={id} status={run.status} />
        </div>
      )}
      {events.length > 0 && (
        <Card
          title="Flow"
          description="Every state here is read off the audit trail below — there is no second source for it, so the picture and the evidence cannot disagree."
        >
          {/* The status decides whether a node left mid-flight reads as 'running' or
              'interrupted' — a finished run must never show a live spinner. */}
          <FlowGraph runId={id} events={events} runStatus={run?.status} />
        </Card>
      )}

      <div className="mt-6" />

      <Card description={`run ${id}`}>
      {events.length === 0 ? (
        <Empty>No events recorded for this run.</Empty>
      ) : (
        <table className="w-full text-sm">
          <thead className="text-xs text-muted">
            <tr className="border-b border-line">
              <th className="pb-2 text-left font-medium">#</th>
              <th className="pb-2 text-left font-medium">Event</th>
              <th className="pb-2 text-left font-medium">Node</th>
              <th className="pb-2 text-left font-medium">Control</th>
              <th className="pb-2 text-left font-medium">When</th>
            </tr>
          </thead>
          <tbody>
            {events.map((event) => (
              <tr key={event.seq} className="border-b border-line last:border-0">
                <td className="mono py-2 text-xs text-muted">{event.seq}</td>
                <td className="py-2">
                  {event.kind}
                  {/* Surfaced inline rather than left in the payload: a run that executed
                      model-written code under weakened isolation should be readable as such
                      without anyone expanding a JSON blob. */}
                  {event.kind === "sandbox_isolation" && (
                    <span
                      className={`ml-2 rounded-md px-2 py-0.5 text-xs font-medium ${
                        event.payload.state === "degraded"
                          ? "bg-amber-500/15 text-amber-700 dark:text-amber-300"
                          : "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300"
                      }`}
                      title={
                        Array.isArray(event.payload.gaps)
                          ? (event.payload.gaps as string[]).join("; ")
                          : undefined
                      }
                    >
                      {String(event.payload.state)}
                    </span>
                  )}

                  {/* Why the run stopped, on the row rather than behind an expander.
                      A trail that shows `node_started` and then nothing forces the reader to
                      infer a failure from a missing row and never tells them the reason —
                      which, for a record whose whole purpose is answering "what happened",
                      is the one question it must not duck. */}
                  {(event.kind === "node_failed" || event.kind === "run_failed") && (
                    <div className="mt-1 rounded-md border border-red-500/25 bg-red-500/6 px-2 py-1 text-xs text-red-700 dark:text-red-300">
                      <span className="mono font-medium">
                        {String(event.payload.error ?? "error")}
                      </span>
                      {event.payload.message ? (
                        <span className="ml-2">{String(event.payload.message)}</span>
                      ) : null}
                    </div>
                  )}

                  {event.kind === "aborting" && event.payload.reason ? (
                    <span className="ml-2 text-xs text-amber-700 dark:text-amber-400">
                      {String(event.payload.reason)}
                    </span>
                  ) : null}
                </td>
                <td className="mono py-2 text-xs text-muted">
                  {event.node_id ?? "—"}
                </td>
                <td className="py-2">
                  {/* An em dash means this event represents no external effect at all. It
                      never means "we did not check" — invariant 11. */}
                  {event.control_mode === null ? (
                    <span className="text-xs text-muted">—</span>
                  ) : (
                    <span
                      className={`rounded-md px-2 py-0.5 text-xs font-medium ${
                        event.control_mode === "authorized"
                          ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300"
                          : "bg-amber-500/15 text-amber-700 dark:text-amber-300"
                      }`}
                    >
                      {event.control_mode}
                    </span>
                  )}
                </td>
                <td className="py-2 text-muted">
                  {new Date(event.occurred_at).toLocaleTimeString()}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      </Card>
    </div>
  );
}
