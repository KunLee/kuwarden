/** Every run, newest first. */

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import {
  Card,
  Empty,
  PageHeader,
  RiskBadge,
  Row,
  StatusBadge,
  Table,
} from "../components/ui";
import type { Run } from "../types";

/**
 * How long the run took, or that it is still going.
 *
 * Reads `ended_at` rather than subtracting from the current time, so a finished run shows the
 * same figure tomorrow as it did the moment it landed.
 */
function duration(run: Run): string {
  if (!run.ended_at) {
    return run.status === "running" || run.status === "suspended" ? "in flight" : "—";
  }
  const ms = new Date(run.ended_at).getTime() - new Date(run.created_at).getTime();
  if (ms < 0) return "—";
  const seconds = Math.round(ms / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ${seconds % 60}s`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

export function Runs() {
  const [runs, setRuns] = useState<Run[]>([]);

  useEffect(() => {
    void api
      .listRuns()
      .then(setRuns)
      .catch(() => setRuns([]));
  }, []);

  return (
    <div>
      <PageHeader title="Runs" description={`${runs.length} recorded`} />

      <Card>
        {runs.length === 0 ? (
          <Empty>No runs recorded.</Empty>
        ) : (
          // Application first: it is what tells two runs apart once more than one
          // application is registered, and a ticket id on its own does not.
          <Table
            head={["Application", "Ticket", "Risk", "Status", "Policy", "Started"]}
          >
            {runs.map((run) => (
              <Row key={run.id}>
                <td className="py-3 align-top">
                  <Link
                    to={`/runs/${run.id}`}
                    className="font-medium text-accent hover:underline"
                  >
                    {run.app_name}
                  </Link>
                </td>
                {/* Two lines rather than a wider column: the ticket id is what an operator
                    matches against their board, and the system it came from is context they
                    need occasionally and never scan for. */}
                <td className="py-3 align-top">
                  <div className="font-medium">{run.ticket_id}</div>
                  <div className="text-[12px] text-faint">{run.ticket_system}</div>
                </td>
                <td className="py-3 align-top">
                  <RiskBadge tier={run.risk_tier} />
                </td>
                <td className="py-3 align-top">
                  <StatusBadge status={run.status} />
                </td>
                {/* The pinned policy commit. Without it an audit record says what happened
                    but not what was permitted at the time — ADR 0003. */}
                <td className="mono py-3 align-top text-[12px] text-faint">
                  {run.policy_commit.slice(0, 10)}
                </td>
                <td className="py-3 align-top text-[13px] text-muted">
                  <div>{new Date(run.created_at).toLocaleString()}</div>
                  <div className="text-[12px] text-faint">{duration(run)}</div>
                </td>
              </Row>
            ))}
          </Table>
        )}
      </Card>
    </div>
  );
}
