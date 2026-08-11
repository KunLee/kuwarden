/**
 * The approval gate.
 *
 * This is the page the notification email links to, and the only place a decision can be
 * made. Three things about it are deliberate and should survive a redesign.
 *
 * **The caveats sit above the buttons.** Everything weaker than it looks about this evidence —
 * tests graded by our own sandbox rather than by CI, degraded isolation, no pinned policy —
 * is rendered before the approver can reach a control. A qualification placed below the fold
 * is a qualification written for the record rather than for the reader.
 *
 * **The digest is submitted with the decision.** It binds the approval to the exact document
 * rendered here; the API recomputes it and refuses if the run has moved on. That turns
 * "approved run X" into "approved these facts about run X" — ADR 0003 §6.
 *
 * **Reject is not styled as a lesser action.** A gate whose refuse path looks like a cancel
 * button is a gate that gets approved by default.
 */

import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { useCan } from "../auth";
import { Banner, Button, Card, Field, Input } from "./ui";
import type { Evidence } from "../types";

export function ApprovalGate({ runId, status }: { runId: string; status: string }) {
  const canApprove = useCan("approver");
  const [evidence, setEvidence] = useState<Evidence | null>(null);
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<boolean | null>(null);

  useEffect(() => {
    void api
      .runEvidence(runId)
      .then(setEvidence)
      .catch(() => setEvidence(null));
  }, [runId]);

  async function decide(approved: boolean) {
    if (!evidence) return;
    setBusy(true);
    setError(null);
    try {
      await api.decide(runId, approved, evidence.digest, comment);
      setDone(approved);
    } catch (e) {
      // A 409 here is the digest check firing, which is a normal event rather than a fault:
      // the run produced new evidence while this page was open. Re-fetch so the approver
      // reads the current document instead of retrying against a stale one.
      if (e instanceof ApiError && e.status === 409) {
        setEvidence(await api.runEvidence(runId).catch(() => null));
      }
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  if (status !== "suspended" || !evidence) return null;

  const doc = evidence.document;

  return (
    <Card
      title="This run is waiting for a decision"
      description="Your approval is recorded against the evidence below, identified by its digest. If the run produces new evidence before you decide, the decision is refused and you are shown the current document."
    >
      <div className="grid gap-5">
        {done !== null ? (
          <Banner tone={done ? "ok" : "warn"}>
            Recorded. This run was {done ? "approved" : "rejected"}.
          </Banner>
        ) : (
          <>
            {/* Above the controls, always. See the file docstring. */}
            {doc.caveats.length > 0 && (
              <Banner tone="warn">
                <strong className="font-semibold">
                  Read before deciding — this evidence is weaker than it appears:
                </strong>
                <ul className="mt-2 list-disc space-y-1 pl-5">
                  {doc.caveats.map((caveat) => (
                    <li key={caveat}>{caveat}</li>
                  ))}
                </ul>
              </Banner>
            )}

            <dl className="grid gap-x-8 gap-y-3 text-[13px] sm:grid-cols-2">
              <Fact label="Ticket" value={`${doc.ticket.system} ${doc.ticket.id}`} />
              <Fact label="Risk tier" value={doc.risk_tier} />
              <Fact
                label="Tests"
                value={
                  doc.tests.exit_code === undefined
                    ? "no verdict recorded"
                    : `exit ${doc.tests.exit_code} — run by ${
                        doc.tests.source === "ci" ? "the project's CI" : "KuWarden's sandbox"
                      }`
                }
              />
              <Fact label="Policy pinned at" value={doc.policy_commit} mono />
            </dl>

            <details className="rounded-xl border border-line px-4 py-3">
              <summary className="cursor-pointer text-[13px] font-medium">
                The full evidence document ({doc.events.length} events)
              </summary>
              <pre className="mono mt-3 max-h-96 overflow-auto text-xs leading-relaxed text-muted">
                {JSON.stringify(doc, null, 2)}
              </pre>
            </details>

            <p className="mono text-xs text-muted">digest {evidence.digest}</p>

            <Field
              label="Comment"
              hint="Optional, and kept in the audit trail alongside your decision."
            >
              <Input
                value={comment}
                onChange={(e) => setComment(e.target.value)}
                placeholder="Why you are approving or rejecting"
              />
            </Field>

            {error && <Banner tone="error">{error}</Banner>}

            <div className="flex gap-3">
              <Button variant="primary" disabled={!canApprove || busy} onClick={() => decide(true)}>
                Approve
              </Button>
              {/* Same weight as approve. A refuse path that looks like a cancel button
                  produces a gate that is approved by default. */}
              <Button variant="danger" disabled={!canApprove || busy} onClick={() => decide(false)}>
                Reject
              </Button>
              {!canApprove && (
                <span className="self-center text-[13px] text-muted">
                  You can read this run, but deciding requires the approver role.
                </span>
              )}
            </div>
          </>
        )}
      </div>
    </Card>
  );
}

function Fact({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <dt className="text-xs text-muted">{label}</dt>
      <dd className={mono ? "mono mt-0.5 text-xs" : "mt-0.5"}>{value}</dd>
    </div>
  );
}
