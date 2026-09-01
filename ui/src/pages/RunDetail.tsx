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
import {
  BackLink,
  Banner,
  Button,
  Card,
  ConfirmDialog,
  Empty,
  PageHeader,
} from "../components/ui";
import { useCan } from "../auth";
import { ApprovalGate } from "../components/ApprovalGate";
import { FlowGraph } from "../components/FlowGraph";
import { RunChainButton } from "../components/RunChain";
import { TicketGraph } from "../components/TicketGraph";
import type { Run, RunEvent } from "../types";

/** Statuses that can still produce another event. Anything else is terminal. */
const IN_FLIGHT = ["running", "suspended"];

export function RunDetail() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const canApprove = useCan("approver");
  // Terminating is an operational act on the platform, not a judgment about the change,
  // so it is admin rather than approver. Rejecting at the gate is the approver's verb.
  const canTerminate = useCan("admin");
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [run, setRun] = useState<Run | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [live, setLive] = useState(false);
  const [confirmKill, setConfirmKill] = useState(false);

  /**
   * Start the same ticket again.
   *
   * A *new* run, deliberately — not a resume and not a retry of this one. The audit trail is
   * append-only, so a run that failed stays failed and keeps its record; re-running produces
   * a second record that can be compared with the first. Reusing the id would overwrite the
   * evidence of what went wrong, which is the one thing that must not happen.
   */
  /**
   * Stop a run that is going nowhere.
   *
   * Abrupt on purpose. The workflow is terminated rather than cancelled, because a Temporal
   * cancellation arrives as a `BaseException` the flow does not catch — compensation would
   * not run either way, and terminating is at least honest about it. The branch stays on the
   * remote, and the server tells us its name so this can say so rather than let an operator
   * discover it later.
   */
  async function terminate() {
    if (!run) return;
    setBusy(true);
    setMessage(null);
    try {
      const result = await api.terminateRun(run.id);
      setConfirmKill(false);
      setMessage(
        result.branch_left_behind
          ? `Run stopped. Branch ${result.branch_left_behind} was not deleted — compensation does not run on a terminate, so remove it by hand if you do not want it.`
          : "Run stopped. Nothing had been pushed, so there is no branch to clean up.",
      );
      // List and filter, the same way the poll below does. There is no single-run endpoint
      // and that is deliberate — this line called one that was never built, so the file has
      // not typechecked since it was written. Refreshed here rather than left to the next
      // poll tick because the poll stops as soon as it sees a terminal status, and the whole
      // point of this handler is that the operator sees the new one immediately.
      // List and filter, the same way the poll below does. There is no single-run endpoint
      // and that is deliberate — this line called one that was never built, so the file has
      // not typechecked since it was written. Refreshed here rather than left to the next
      // poll tick because the poll stops as soon as it sees a terminal status, and the whole
      // point of this handler is that the operator sees the new one immediately.
      const runs = await api.listRuns();
      setRun(runs.find((r) => r.id === run.id) ?? run);
    } catch (e) {
      setMessage(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

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
      <div className="mb-6">
        <BackLink to="/runs">All runs</BackLink>
      </div>

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
            {run && canTerminate && IN_FLIGHT.includes(run.status) && (
              <Button variant="danger" onClick={() => setConfirmKill(true)} disabled={busy}>
                Stop this run
              </Button>
            )}
            {run && canApprove && (
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

      <ConfirmDialog
        open={confirmKill}
        onOpenChange={setConfirmKill}
        title={`Stop run ${run?.ticket_id ?? ""}?`}
        confirmLabel={busy ? "Stopping…" : "Stop the run"}
        onConfirm={terminate}
        busy={busy}
      >
        {/* Says what will be left behind rather than only what will stop. An operator who
            learns about the orphaned branch afterwards has already lost the chance to
            decide. */}
        <p>
          The flow stops immediately and cleanup does <strong>not</strong> run, so any branch
          this run pushed stays on the remote for you to inspect or delete.
        </p>
        <p className="mt-3">
          The audit trail is append-only: everything recorded so far is kept, and a
          <span className="mono"> run_terminated </span>
          row is added naming you.
        </p>
      </ConfirmDialog>
      {run && (
        <div className="mb-6">
          <ApprovalGate runId={id} status={run.status} />
        </div>
      )}
      {events.length > 0 && (
        <Card
          title="Flow"
          description="Every state here is read off the audit trail below — there is no second source for it, so the picture and the evidence cannot disagree."
          /* The topology is the same for every run; what each run actually executed is not.
             The button opens that — the real sequence, with the repeats. */
          actions={<RunChainButton events={events} runId={id} />}
        >
          {/* The status decides whether a node left mid-flight reads as 'running' or
              'interrupted' — a finished run must never show a live spinner. */}
          <FlowGraph runId={id} events={events} runStatus={run?.status} />
        </Card>
      )}

      <div className="mt-6" />

      {/* Every run for this ticket, not just this one. Four runs for ticket 50 branched from
          the same base, all edited the same file, none could see the others, and two were
          merged — the second by hand into a state nothing had verified. Each was individually
          green; the collision only exists between them, which is why it needs its own view. */}
      <Card
        title="This ticket"
        description="Runs are columns in the order they started, files are rows. A row with more than one mark is a file two runs changed without seeing each other."
      >
        <TicketGraph runId={id} />
      </Card>

      <div className="mt-6" />

      <Card description={`run ${id}`}>
      {events.length === 0 ? (
        <Empty>No events recorded for this run.</Empty>
      ) : (
        // Wrapped, the way the shared `Table` primitive is. Payload text — a CI detail
        // sentence, a branch name, a failure message — has no width bound of its own, and an
        // unwrapped table widens the card and then the page rather than scrolling itself.
        <div className="overflow-x-auto">
        <table className="w-full min-w-[40rem] text-sm">
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
        </div>
      )}
      </Card>
    </div>
  );
}
