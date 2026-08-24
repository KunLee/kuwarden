/**
 * Which verifiers may stop a change.
 *
 * **Off means advisory, not skipped.** A disarmed verifier still runs, still reads the diff,
 * and still records its findings into the audit trail — it simply cannot abort the run.
 * Skipping it would save a model call and destroy the evidence, which for a product whose
 * value is the record is the wrong trade.
 *
 * The state is deliberately legible at a glance: a disarmed verifier is not a greyed-out row
 * that reads as "off", it is a row that says what it will still do. An operator who disarms a
 * gate should be able to see, without asking anyone, exactly how much weaker it now is.
 */

import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import { useCan } from "../auth";
import { Banner, Card, Switch } from "./ui";

/** What each angle actually looks at, so the toggle is not four opaque identifiers. */
const ANGLES: Record<string, string> = {
  correctness: "Does the change do what the ticket asked?",
  security: "What attack surface does it open?",
  regression_risk: "What else might it break?",
  test_evidence: "Does the passing suite mean anything?",
};

type Row = { name: string; blocking: boolean };

export function Verifiers({ appId }: { appId: string }) {
  const canAdmin = useCan("admin");
  const [rows, setRows] = useState<Row[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void api
      .readVerifiers(appId)
      .then((r) => setRows(r.verifiers))
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)));
  }, [appId]);

  async function toggle(name: string, blocking: boolean) {
    setBusy(name);
    setError(null);
    try {
      const result = await api.setVerifiers(appId, { [name]: blocking });
      setRows(result.verifiers);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  const advisory = (rows ?? []).filter((r) => !r.blocking);

  return (
    <Card
      title="Verification"
      description="Four independent reviews, each in a fresh context. Turning one off makes it advisory — it still runs and still records what it found, but it cannot stop a change."
    >
      {error && (
        <div className="mb-4">
          <Banner tone="error">{error}</Banner>
        </div>
      )}

      {advisory.length > 0 && (
        <div className="mb-4">
          {/* Stated plainly and above the controls. A weakened gate that reads as normal is
              the failure this whole page exists to prevent. */}
          <Banner tone="warn">
            {advisory.length === 1
              ? `${label(advisory[0].name)} cannot stop a change.`
              : `${advisory.length} verifiers cannot stop a change.`}{" "}
            Their findings are still recorded, and the approval page says so.
          </Banner>
        </div>
      )}

      {rows === null ? (
        <p className="text-sm text-muted">Loading…</p>
      ) : (
        <ul className="grid gap-2.5">
          {rows.map((row) => (
            <li
              key={row.name}
              className="flex items-start gap-4 rounded-xl border border-line px-4 py-3"
            >
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium">{label(row.name)}</p>
                <p className="mt-0.5 text-xs text-muted">{ANGLES[row.name]}</p>
                <p className="mono mt-1.5 text-[11px] text-faint">
                  {row.blocking ? "blocks a change it falsifies" : "advisory — records, never blocks"}
                </p>
              </div>

              <div className="mt-0.5">
                <Switch
                  checked={row.blocking}
                  onCheckedChange={(next) => void toggle(row.name, next)}
                  disabled={!canAdmin || busy === row.name}
                  label={`${label(row.name)} may block a change`}
                />
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

/** `test_evidence` reads badly in a UI; the underlying id stays what the config uses. */
function label(name: string): string {
  return name.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}
