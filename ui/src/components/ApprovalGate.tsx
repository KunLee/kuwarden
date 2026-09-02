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
 * **So do the verifier findings, including the ones from verifiers that passed.** This page
 * used to show a count — "3 of 4 passed" — and nothing else. On ticket 50 that count was
 * true, the change shipped, and it did not implement the feature: `correctness` had returned
 * a passing verdict while writing, in its findings, exactly what was missing. A verdict is a
 * judgement; the findings are what it was a judgement about, and only one of the two was
 * reaching the person deciding.
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
      {/* `min-w-0` on the children, not decoration. Grid items default to `min-width: auto`,
          which refuses to shrink below the intrinsic width of their content — so the evidence
          document's long JSON lines widen the item, the card, and then the page, and the
          `overflow-auto` on the <pre> never gets the chance to scroll. */}
      <div className="grid gap-5 [&>*]:min-w-0">
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

            {/* The one thing on this page that is not a document. Every check above verifies
                form; opening the change and using it is the only way to see whether it does
                what was asked, and ticket 50 passed every gate and shipped a feature that did
                not work. Thirty seconds here is the cheapest control this product has. */}
            {doc.preview_url && (
              <a
                href={doc.preview_url}
                target="_blank"
                rel="noreferrer"
                className="flex items-center justify-between gap-4 rounded-xl border border-accent/30 bg-accent/5 px-4 py-3 transition hover:bg-accent/10"
              >
                <span className="min-w-0">
                  <span className="block text-[13px] font-semibold">Open this change, running</span>
                  <span className="mt-0.5 block truncate text-[12px] text-muted">
                    {doc.preview_url}
                  </span>
                </span>
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor"
                     strokeWidth="1.6" className="shrink-0" aria-hidden="true">
                  <path d="M6 3h7v7M13 3L6.5 9.5" />
                  <path d="M11 9v4H3V5h4" />
                </svg>
              </a>
            )}

            {/* Above the controls, with the caveats, and never collapsed. A finding the
                approver has to expand is a finding they will not read. */}
            {doc.verifications?.some((v) => v.findings.length > 0) && (
              <Card title="What the verifiers wrote">
                <ul className="grid gap-4">
                  {doc.verifications
                    .filter((v) => v.findings.length > 0)
                    /* Blocking first: the review that refused this change is the one to
                       read before the ones that did not. */
                    .sort((a, b) => Number(b.blocks) - Number(a.blocks))
                    .map((v) => (
                      <li key={v.verifier} className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="text-[13px] font-semibold">{v.verifier}</span>
                          <span
                            className={`inline-flex rounded-lg px-2.5 py-1 text-[12px] font-medium ${
                              v.blocks
                                ? "bg-red-500/12 text-red-700 dark:text-red-300"
                                : "bg-slate-500/10 text-slate-600 dark:text-slate-300"
                            }`}
                          >
                            {v.blocks ? "blocks" : "passes"}
                          </span>
                        </div>
                        <ul className="mt-2 grid gap-2 text-[13px] leading-relaxed text-slate-600 dark:text-slate-300">
                          {/* Blocking first, and labelled. The verdict is computed from these
                              severities, so showing them lets an approver check the derivation
                              instead of taking it — and lets them overrule an "advisory" that
                              reads to them like a reason not to ship. */}
                          {(v.graded ?? v.findings.map((detail) => ({ detail, severity: "note" as const })))
                            .slice()
                            .sort((a, b) =>
                              Number(b.severity === "blocking") - Number(a.severity === "blocking"),
                            )
                            .map((f) => (
                              <li key={f.detail} className="flex gap-2">
                                <span
                                  className={`mt-0.5 shrink-0 rounded px-1.5 py-0.5 text-[10.5px] font-medium uppercase tracking-wide ${
                                    f.severity === "blocking"
                                      ? "bg-red-500/12 text-red-700 dark:text-red-300"
                                      : f.severity === "advisory"
                                        ? "bg-amber-500/12 text-amber-800 dark:text-amber-300"
                                        : "bg-slate-500/10 text-slate-500 dark:text-slate-400"
                                  }`}
                                >
                                  {f.severity}
                                </span>
                                <span className="min-w-0">{f.detail}</span>
                              </li>
                            ))}
                        </ul>
                      </li>
                    ))}
                </ul>
              </Card>
            )}

            <dl className="grid min-w-0 gap-x-8 gap-y-3 text-[13px] sm:grid-cols-2 [&>*]:min-w-0">
              <Fact label="Ticket" value={`${doc.ticket.system} ${doc.ticket.id}`} />
              {/* The reason, not just the tier. An approver asked for a second signature
                  on a change the ticket described as routine needs to know what raised
                  it — and until this was added the page showed the tier intake guessed
                  before any code existed, which could be two levels below the one the
                  gate was actually enforcing. */}
              <Fact
                label="Risk tier"
                value={
                  doc.provisional_risk_tier &&
                  doc.provisional_risk_tier !== doc.risk_tier
                    ? `${doc.risk_tier} — raised from ${doc.provisional_risk_tier}: ${doc.risk_tier_reason}`
                    : doc.risk_tier
                }
              />
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

            <details className="min-w-0 rounded-xl border border-line px-4 py-3">
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
      {/* break-all: values like `unpinned:no-policy-loader` and a 40-char SHA have no
          spaces to wrap at, so without this they push their grid cell wider than the page. */}
      <dd className={mono ? "mono mt-0.5 break-all text-xs" : "mt-0.5 break-words"}>{value}</dd>
    </div>
  );
}
