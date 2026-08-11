/**
 * The Dashboard: what is happening right now.
 *
 * The tiles deliberately omit vanity counts. ADR 0002 is explicit that "PRs opened" and
 * "tickets closed" are the wrong things to measure — they are exactly the numbers an agent
 * learns to optimise. What an operator needs at a glance is what is waiting on a human and
 * what failed.
 */

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
import type { Application, Run } from "../types";

function Tile({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: string | number;
  hint?: string;
  tone?: "attention";
}) {
  return (
    <div className="rounded-2xl border border-line bg-surface px-6 py-5">
      <div className="text-[12px] font-medium text-muted">{label}</div>
      <div
        className={`mt-1.5 text-[28px] font-semibold tabular-nums tracking-[-0.02em] ${
          tone === "attention" && value !== 0 ? "text-accent" : ""
        }`}
      >
        {value}
      </div>
      {hint && <div className="mt-0.5 text-[12px] text-faint">{hint}</div>}
    </div>
  );
}

export function Dashboard() {
  const [runs, setRuns] = useState<Run[]>([]);
  const [apps, setApps] = useState<Application[]>([]);
  const [healthy, setHealthy] = useState<boolean | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        setHealthy((await api.health()).status === "ok");
        setRuns(await api.listRuns());
        setApps(await api.listApplications());
      } catch {
        setHealthy(false);
      }
    })();
  }, []);

  const waiting = runs.filter((r) => r.status === "suspended").length;
  const failed = runs.filter((r) => r.status === "failed" || r.status === "aborted").length;

  return (
    <div>
      <PageHeader
        title="Dashboard"
        description="What is running, what is waiting on a person, and what did not survive."
      />

      <div className="mb-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Tile label="Applications" value={apps.length} />
        <Tile
          label="Waiting on a human"
          value={waiting}
          hint="suspended at an approval gate"
          tone="attention"
        />
        <Tile label="Failed" value={failed} hint="aborted or compensated" />
        <Tile
          label="Engine"
          value={healthy === null ? "…" : healthy ? "reachable" : "unreachable"}
        />
      </div>

      <Card title="Recent runs" description="Newest first">
        {runs.length === 0 ? (
          <Empty>No runs yet. Trigger one once an application has its credentials.</Empty>
        ) : (
          <Table head={["Ticket", "Risk", "Status", "Started"]}>
            {runs.slice(0, 10).map((run) => (
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
