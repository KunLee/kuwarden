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
          <Table head={["Ticket", "Risk", "Status", "Policy", "Started"]}>
            {runs.map((run) => (
              <Row key={run.id}>
                <td className="py-3">
                  <Link
                    to={`/runs/${run.id}`}
                    className="font-medium text-accent hover:underline"
                  >
                    {run.ticket_system}:{run.ticket_id}
                  </Link>
                </td>
                <td className="py-3">
                  <RiskBadge tier={run.risk_tier} />
                </td>
                <td className="py-3">
                  <StatusBadge status={run.status} />
                </td>
                {/* The pinned policy commit. Without it an audit record says what happened
                    but not what was permitted at the time — ADR 0003. */}
                <td className="mono py-3 text-[12px] text-faint">
                  {run.policy_commit.slice(0, 10)}
                </td>
                <td className="py-3 text-[13px] text-muted">
                  {new Date(run.created_at).toLocaleString()}
                </td>
              </Row>
            ))}
          </Table>
        )}
      </Card>
    </div>
  );
}
