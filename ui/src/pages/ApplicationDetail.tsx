/**
 * One application: its credentials, its capability probe, and deletion.
 *
 * The credential section is write-only. It can report that a credential exists and can
 * replace or delete one, and there is no code path — here or in the API — that returns a
 * stored value. A credential retrievable through a UI is a credential that eventually is.
 */

import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, ApiError } from "../api";
import { useCan } from "../auth";
import { Ticketing } from "../components/Ticketing";
import { Banner, Button, Card, Field, Input, Select } from "../components/ui";
import type { Application, CredentialState, ProbeResult } from "../types";

/** What each credential is for, so an operator does not have to guess from the identifier. */
const KIND_LABELS: Record<string, string> = {
  "ticket.read_write": "Ticket system — read work items, post comments",
  "scm.read": "Source control — read code",
  "scm.write_branch": "Source control — push the agent's own branch",
  "scm.pull_request": "Source control — open pull requests",
  "scm.merge": "Source control — merge (Flow Engine only, never a node)",
  "ci.trigger": "CI — trigger and poll builds",
  deploy: "Deployment (Flow Engine only, never a node)",
  "llm.api_key": "Model provider",
};

export function ApplicationDetail() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const canAdmin = useCan("admin");
  const canApprove = useCan("approver");
  const [ticketId, setTicketId] = useState("");

  const [app, setApp] = useState<Application | null>(null);
  const [creds, setCreds] = useState<CredentialState | null>(null);
  const [probe, setProbe] = useState<ProbeResult | null>(null);
  const [checks, setChecks] = useState<Record<
    string,
    { ok: boolean; target?: string; detail: string }
  > | null>(null);
  const [message, setMessage] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  const [kind, setKind] = useState("scm.read");
  const [value, setValue] = useState("");
  // Which action is in flight, not merely whether one is. A shared boolean made every
  // button on the page announce itself as running the moment any one of them was clicked.
  const [busy, setBusy] = useState<"store" | "run" | "check" | "probe" | "point" | null>(
    null,
  );
  const [movingPoint, setMovingPoint] = useState(false);
  const [newPoint, setNewPoint] = useState("gated_merge");
  const [pointReason, setPointReason] = useState("");

  async function refresh() {
    try {
      const apps = await api.listApplications();
      setApp(apps.find((a) => a.id === id) ?? null);
      setCreds(await api.listCredentials(id));
    } catch (e) {
      setMessage({ tone: "error", text: e instanceof ApiError ? e.message : String(e) });
    }
  }

  useEffect(() => {
    void refresh();
  }, [id]);

  async function store() {
    setBusy("store");
    setMessage(null);
    try {
      await api.storeCredential(id, kind, value);
      // Cleared immediately. The value is not kept in component state, and there is nothing
      // to re-render it from — the API will not return it.
      setValue("");
      setMessage({ tone: "ok", text: "Stored, encrypted. It cannot be read back." });
      await refresh();
    } catch (e) {
      setMessage({ tone: "error", text: e instanceof ApiError ? e.message : String(e) });
    } finally {
      setBusy(null);
    }
  }

  async function start() {
    setBusy("run");
    setMessage(null);
    try {
      const started = await api.startRun(id, ticketId);
      setTicketId("");
      setMessage({
        tone: "ok",
        text: `Run ${started.run_id} started. Watch it on the Runs page.`,
      });
    } catch (e) {
      setMessage({ tone: "error", text: e instanceof ApiError ? e.message : String(e) });
    } finally {
      setBusy(null);
    }
  }

  async function checkConnections() {
    setBusy("check");
    setMessage(null);
    try {
      setChecks(await api.checkConnections(id));
    } catch (e) {
      setMessage({ tone: "error", text: e instanceof ApiError ? e.message : String(e) });
    } finally {
      setBusy(null);
    }
  }

  async function runProbe() {
    setBusy("probe");
    setMessage(null);
    try {
      setProbe(await api.probe(id));
    } catch (e) {
      setMessage({ tone: "error", text: e instanceof ApiError ? e.message : String(e) });
    } finally {
      setBusy(null);
    }
  }

  async function moveControlPoint() {
    setBusy("point");
    setMessage(null);
    try {
      const result = await api.changeControlPoint(id, newPoint, pointReason);
      setMovingPoint(false);
      setPointReason("");
      setMessage({
        tone: "ok",
        text: result.changed
          ? `Control point is now ${result.integration_model}. ` +
            `${result.runs_predating_this_change ?? 0} earlier run(s) were governed by the ` +
            "previous one, and their records do not say so."
          : "Already set to that; nothing recorded.",
      });
      await refresh();
    } catch (e) {
      setMessage({ tone: "error", text: e instanceof ApiError ? e.message : String(e) });
    } finally {
      setBusy(null);
    }
  }

  async function remove() {
    if (!confirm(`Delete ${app?.name}? Its stored credentials are deleted with it.`)) return;
    try {
      await api.deleteApplication(id);
      navigate("/applications");
    } catch (e) {
      setMessage({ tone: "error", text: e instanceof ApiError ? e.message : String(e) });
    }
  }

  if (!app) return <Card>Loading…</Card>;

  return (
    <div className="space-y-6">
      <Card
        title={app.name}
        description={app.repo_url}
        actions={
          canAdmin && (
            <Button variant="danger" onClick={remove}>
              Delete
            </Button>
          )
        }
      >
        <dl className="grid grid-cols-2 gap-4 text-sm sm:grid-cols-3">
          <div>
            <dt className="text-xs text-muted">Control point</dt>
            <dd className="mono mt-0.5">{app.integration_model}</dd>
            {canAdmin && (
              <button
                type="button"
                className="mt-1 text-xs text-accent hover:underline"
                onClick={() => setMovingPoint((v) => !v)}
              >
                {movingPoint ? "Cancel" : "Change"}
              </button>
            )}
          </div>
          <div>
            <dt className="text-xs text-muted">Registered</dt>
            <dd className="mt-0.5">{new Date(app.created_at).toLocaleString()}</dd>
          </div>
          <div>
            <dt className="text-xs text-muted">Application id</dt>
            <dd className="mono mt-0.5 text-xs">{app.id}</dd>
          </div>
        </dl>

        {movingPoint && (
          <div className="mt-5 grid gap-4 border-t border-line pt-5 sm:grid-cols-[auto_1fr_auto] sm:items-end">
            <Field label="New control point">
              <Select value={newPoint} onChange={(e) => setNewPoint(e.target.value)}>
                {["kuwarden_deploys", "gated_merge", "gated_deployment"].map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </Select>
            </Field>
            <Field
              label="Reason"
              hint="Recorded permanently. Runs that predate the change were governed by the old control point, and nothing in their record says so."
            >
              <Input
                value={pointReason}
                placeholder="registered with the wrong model"
                onChange={(e) => setPointReason(e.target.value)}
              />
            </Field>
            <Button
              variant="primary"
              onClick={moveControlPoint}
              disabled={busy !== null || !pointReason}
            >
              Change
            </Button>
          </div>
        )}
      </Card>

      {/* Pinned to the bottom, not the top.
          The actions that produce these messages are spread down a long page — Probe and
          credential-store both sit well below the fold — so a banner in the normal flow
          reported their failures off-screen and the button looked like it did nothing. The
          first fix stuck it to the top, where it collided with the header, which is also
          `sticky z-10`. The bottom is empty, so nothing has to yield.
          `pointer-events-none` on the wrapper keeps the strip from swallowing clicks on the
          page behind it; the banner itself takes them back so the text stays selectable. */}
      {message && (
        <div className="pointer-events-none fixed inset-x-0 bottom-0 z-20 flex justify-center p-4">
          <div className="pointer-events-auto w-full max-w-3xl rounded-xl bg-surface shadow-lg">
            <Banner tone={message.tone}>{message.text}</Banner>
          </div>
        </div>
      )}

      <Ticketing appId={id} />

      <Card
        title="Start a run"
        description="Hand one ticket to the Flow Engine. A webhook receiver comes later; this is the manual path, and it starts exactly the same workflow."
      >
        <div className="grid gap-4 sm:grid-cols-[1fr_auto] sm:items-end">
          <Field
            label="Ticket"
            hint="The key as the ticket system knows it — PAY-1234 for Jira, the work item id for Azure DevOps."
          >
            <Input
              value={ticketId}
              placeholder="PAY-1234"
              onChange={(e) => setTicketId(e.target.value)}
            />
          </Field>
          <Button
            variant="primary"
            onClick={start}
            disabled={!canApprove || busy !== null || !ticketId}
          >
            {busy === "run" ? "Starting…" : "Start run"}
          </Button>
        </div>
      </Card>

      <Card
        title="Credentials"
        description="Encrypted before they reach the database. No endpoint returns a stored value."
      >
        <div className="grid gap-4 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
          <Field label="Kind">
            <Select value={kind} onChange={(e) => setKind(e.target.value)}>
              {creds?.supported.map((k) => (
                <option key={k} value={k}>
                  {k}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Value" hint={KIND_LABELS[kind]}>
            <Input
              type="password"
              value={value}
              autoComplete="off"
              placeholder="paste the token"
              onChange={(e) => setValue(e.target.value)}
            />
          </Field>
          <Button variant="primary" onClick={store} disabled={!canAdmin || busy !== null || !value}>
            Store
          </Button>
        </div>

        <table className="mt-5 w-full text-sm">
          <tbody>
            {creds?.supported.map((k) => {
              const stored = creds.present.includes(k);
              return (
                <tr key={k} className="border-b border-line last:border-0">
                  <td className="py-2">
                    <span className="mono text-xs">{k}</span>
                    <span className="ml-2 text-xs text-muted">
                      {KIND_LABELS[k]}
                    </span>
                  </td>
                  <td className="py-2 text-right">
                    {stored ? (
                      <span className="text-xs text-emerald-600 dark:text-emerald-400">
                        stored
                      </span>
                    ) : (
                      <span className="text-xs text-muted">—</span>
                    )}
                  </td>
                  <td className="w-20 py-2 text-right">
                    {stored && canAdmin && (
                      <button
                        onClick={async () => {
                          await api.forgetCredential(id, k);
                          await refresh();
                        }}
                        className="text-xs text-red-600 hover:underline dark:text-red-400"
                      >
                        Delete
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </Card>

      <Card
        title="Connections"
        description="Can the stored credentials actually reach each platform? Read-only, and each side is reported separately — a working SCM token with a broken ticket token is the most common half-configured state."
        actions={
          <Button onClick={checkConnections} disabled={!canAdmin || busy !== null}>
            {busy === "check" ? "Checking…" : "Test connections"}
          </Button>
        }
      >
        {!checks ? (
          <p className="text-sm text-muted">Not tested yet.</p>
        ) : (
          <table className="w-full text-sm">
            <tbody>
              {Object.entries(checks).map(([name, result]) => (
                <tr key={name} className="border-t border-line first:border-0">
                  <td className="py-2 pr-4 font-medium capitalize">{name}</td>
                  <td className="py-2 pr-4">
                    <span className={result.ok ? "text-emerald-600" : "text-red-600"}>
                      {result.ok ? "● connected" : "● failed"}
                    </span>
                  </td>
                  <td className="mono py-2 pr-4 text-xs text-muted">{result.target}</td>
                  <td className="py-2 text-xs text-muted">{result.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <Card
        title="Capability probe"
        description="Asks the platform what it can actually do. Runs against the stored SCM credential, so it also answers whether that token works."
        actions={
          <Button onClick={runProbe} disabled={!canAdmin || busy !== null}>
            {busy === "probe" ? "Probing…" : "Probe"}
          </Button>
        }
      >
        {!probe ? (
          <p className="text-sm text-muted">Not probed yet.</p>
        ) : (
          <div className="space-y-3">
            <Banner tone={probe.achievable ? "ok" : "warn"}>
              <strong>{probe.declared}</strong> — {probe.reason}
            </Banner>
            <table className="w-full text-sm">
              <tbody>
                {Object.entries(probe.capabilities.detail).map(([key, detail]) => (
                  <tr key={key} className="border-b border-line last:border-0">
                    <td className="py-2 mono text-xs">{key}</td>
                    <td className="py-2 text-xs text-muted">{detail}</td>
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
