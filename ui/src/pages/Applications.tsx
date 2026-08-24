/**
 * The registered applications, as a list.
 *
 * Nothing is created here. Registration is four steps against three endpoints — the row, its
 * trigger, its credentials — and inlining the first of those on this page produced an
 * application that looked registered and could not run: an operator who stopped at "Register"
 * had declared a repository and configured nothing else.
 *
 * So this page does what a list should: show what exists, show which ones are not finished,
 * and get out of the way.
 */

import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { api, ApiError } from "../api";
import { useCan } from "../auth";
import { Banner, Button, Card, Empty, PageHeader, Row, Table } from "../components/ui";
import type { Application, Trigger } from "../types";

export function Applications() {
  const navigate = useNavigate();
  // A courtesy: the server rejects the POST regardless. A viewer shown a button they cannot
  // use learns nothing.
  const canAdmin = useCan("admin");
  const [apps, setApps] = useState<Application[]>([]);
  //: Which applications have a ticket trigger. An application without one is refused at
  //: Triage on every run, and that is worth saying on the list rather than three clicks in.
  const [triggered, setTriggered] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        const registered = await api.listApplications();
        setApps(registered);
        const withTriggers = await Promise.all(
          registered.map(async (app) => {
            try {
              const triggers: Trigger[] = await api.listTriggers(app.id);
              return triggers.length > 0 ? app.id : null;
            } catch {
              return null;
            }
          }),
        );
        setTriggered(new Set(withTriggers.filter((id): id is string => id !== null)));
      } catch (e) {
        setError(e instanceof ApiError ? e.message : String(e));
      }
    })();
  }, []);

  return (
    <div>
      <PageHeader
        title="Applications"
        description="Each declares its own repository, its ticketing, and where its control point sits."
        actions={
          canAdmin && (
            <Button variant="primary" onClick={() => navigate("/applications/new")}>
              Register an application
            </Button>
          )
        }
      />

      {error && (
        <div className="mb-6">
          <Banner tone="error">{error}</Banner>
        </div>
      )}

      <Card description={`${apps.length} registered`}>
        {apps.length === 0 ? (
          <Empty>
            Nothing registered yet.
            {canAdmin && " Registering one takes four steps and about two minutes."}
          </Empty>
        ) : (
          <Table head={["Name", "Repository", "Control point", "Ticketing", ""]}>
            {apps.map((app) => (
              <Row key={app.id}>
                <td className="py-3 font-medium">{app.name}</td>
                <td className="py-3 text-[13px] text-muted">{app.repo_url}</td>
                <td className="mono py-3 text-[12px]">{app.integration_model}</td>
                <td className="py-3 text-[12px]">
                  {triggered.has(app.id) ? (
                    <span className="text-muted">configured</span>
                  ) : (
                    // Stated rather than left blank: an application with no trigger is
                    // refused at Triage on every run, which is a different thing from one
                    // whose trigger simply is not shown here.
                    <span className="text-amber-700 dark:text-amber-400">
                      none — every run refused
                    </span>
                  )}
                </td>
                <td className="py-3 text-right">
                  <Link
                    to={`/applications/${app.id}`}
                    className="text-[13px] text-accent hover:underline"
                  >
                    Open
                  </Link>
                </td>
              </Row>
            ))}
          </Table>
        )}
      </Card>
    </div>
  );
}
