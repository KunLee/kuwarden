/**
 * Registering an application, end to end, on its own screen.
 *
 * It was a form on the list page, which produced a row that could not do anything: the
 * repository was declared, and ticketing, credentials and the connection check all lived
 * somewhere else. An operator who stopped there had a registration that looked complete and a
 * run that would fail at the first node.
 *
 * **A sequence rather than one form, because the API is a sequence.** Credentials are stored
 * per `app_id` and triggers hang off it, so nothing after step 1 can be sent until step 1 has
 * been accepted. Pretending otherwise would mean holding a token in browser memory while an
 * earlier request is in flight, and the one thing this surface must not do is keep a
 * credential around longer than the moment it is submitted.
 *
 * Each step is committed as it is completed. Leaving halfway leaves a real, partly configured
 * application rather than nothing — which is why the list marks incomplete ones instead of
 * hiding the state, and why leaving offers to discard it.
 *
 * **Discarding is offered, not assumed.** "Registered the application, will get the PAT from
 * someone tomorrow" is a real way to work, not an abandoned wizard, so the prompt presents both
 * outcomes rather than picking one. It also does not cover every exit: navigating away from the
 * sidebar, or closing the tab, leaves the partial application in place. The list flagging it is
 * the backstop, and the honest reason this screen does not claim "cancel means nothing
 * happened".
 */

import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { api, ApiError } from "../api";
import { Banner, Button, Card, Field, Input, PageHeader, Select } from "../components/ui";
import type { IntegrationModel } from "../types";

const MODELS: { value: IntegrationModel; label: string; note: string }[] = [
  {
    value: "gated_deployment",
    label: "gated_deployment",
    note: "The platform pauses its own deployment and asks us. Least invasive, nearly the strongest.",
  },
  {
    value: "gated_merge",
    label: "gated_merge",
    note: "We gate the merge. What the pipeline then deploys is observed, not authorised.",
  },
  {
    value: "kuwarden_deploys",
    label: "kuwarden_deploys",
    note: "We deploy. Strongest audit, but the existing pipeline must be restricted first.",
  },
];

/** What each slot is for, so an operator does not have to guess from the identifier. */
const SLOTS: { kind: string; label: string; hint: string; required: boolean }[] = [
  {
    kind: "ticket.read_write",
    label: "Ticket system",
    hint: "Reads work items and posts the outcome back. Azure DevOps: Work Items → Read & Write.",
    required: true,
  },
  {
    kind: "scm.read",
    label: "Source control — read",
    hint: "Reads the repository tree the Coder works from.",
    required: true,
  },
  {
    kind: "scm.write_branch",
    label: "Source control — write branch",
    hint: "Pushes the agent's own branch. GitHub fine-grained: Contents → Read and write.",
    required: true,
  },
  {
    kind: "scm.pull_request",
    label: "Source control — pull requests",
    hint: "Opens the pull request at Release. GitHub fine-grained: Pull requests → Read and write.",
    required: true,
  },
  {
    kind: "llm.api_key",
    label: "Model provider",
    hint: "The Planner, the Coder and the four verifiers all call it.",
    required: true,
  },
  {
    kind: "ci.read",
    label: "CI — read",
    hint: "Only if kuwarden.yaml declares a `ci:` section. Reads the pipeline verdict back.",
    required: false,
  },
];

type Step = 1 | 2 | 3 | 4;

export function RegisterApplication() {
  const navigate = useNavigate();
  const [step, setStep] = useState<Step>(1);
  const [appId, setAppId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [leaving, setLeaving] = useState(false);

  const [repo, setRepo] = useState({
    name: "",
    scm_provider: "github" as "github" | "azure_repos",
    org: "",
    repo: "",
    project: "",
    integration_model: "" as IntegrationModel | "",
  });

  const [trigger, setTrigger] = useState({
    provider: "azure_devops" as "azure_devops" | "jira",
    organisation: "",
    site: "",
    account_email: "",
    project: "",
    label: "kuwarden-auto",
    ready_state: "",
    max_story_points: "5",
    story_points_field: "",
  });

  // Held only until submitted, then cleared. There is no endpoint that reads one back, and
  // this component must not become the one place a token lingers.
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  const [stored, setStored] = useState<string[]>([]);
  const [checks, setChecks] = useState<Record<
    string,
    { ok: boolean; target?: string; detail: string }
  > | null>(null);

  async function guard(work: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await work();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const createApplication = () =>
    guard(async () => {
      if (!repo.integration_model) {
        throw new ApiError("Choose a control point — ADR 0004 never defaults it.", 422);
      }
      const created = await api.registerApplication({
        ...repo,
        integration_model: repo.integration_model,
        project: repo.project || null,
      });
      setAppId(created.id);
      setStep(2);
    });

  const declareTrigger = () =>
    guard(async () => {
      if (!appId) return;
      await api.declareTrigger(appId, {
        provider: trigger.provider,
        project: trigger.project,
        site: trigger.site || null,
        account_email: trigger.account_email || null,
        organisation: trigger.organisation || null,
        label: trigger.label || null,
        ready_state: trigger.ready_state || null,
        max_story_points: trigger.max_story_points ? Number(trigger.max_story_points) : null,
        story_points_field: trigger.story_points_field || null,
      });
      setStep(3);
    });

  const storeSecret = (kind: string) =>
    guard(async () => {
      if (!appId) return;
      await api.storeCredential(appId, kind, secrets[kind]);
      // Cleared the moment it is accepted. Nothing re-renders it, because nothing can read it
      // back — not from state here, not from any endpoint.
      setSecrets((current) => ({ ...current, [kind]: "" }));
      setStored((current) => [...current, kind]);
    });

  const discard = () =>
    guard(async () => {
      if (!appId) return;
      // Deregistering destroys the stored credentials with it -- `app_credentials` cascades.
      // Refused if a run already references the application, which cannot happen from this
      // screen but is reported rather than swallowed if it ever does.
      await api.deleteApplication(appId);
      navigate("/applications");
    });

  const runChecks = () =>
    guard(async () => {
      if (!appId) return;
      setChecks(await api.checkConnections(appId));
    });

  const isJira = trigger.provider === "jira";
  const selectedModel = MODELS.find((m) => m.value === repo.integration_model);
  const missing = SLOTS.filter((s) => s.required && !stored.includes(s.kind));

  return (
    <div>
      <PageHeader
        title="Register an application"
        description="Four steps, each committed as you finish it. Leaving halfway asks whether to keep the partly configured application or discard it."
        actions={
          <Button
            // Before step 1 is accepted there is nothing to discard, so leaving is just
            // leaving. After it, the choice is real and belongs to the operator.
            onClick={() => {
              if (!appId) return navigate("/applications");
              // Cleared on open, so the dialog only ever shows a failure of the discard
              // itself. A leftover error from step 3 next to a Discard button reads as a
              // reason to discard.
              setError(null);
              setLeaving(true);
            }}
          >
            {appId ? "Leave" : "Cancel"}
          </Button>
        }
      />

      {leaving && appId && (
        <Leaving
          name={repo.name}
          stored={stored.length}
          busy={busy}
          error={error}
          onDiscard={discard}
          onKeep={() => navigate("/applications")}
          onStay={() => setLeaving(false)}
        />
      )}

      <Steps current={step} onGo={setStep} />

      {error && (
        <div className="mb-6">
          <Banner tone="error">{error}</Banner>
        </div>
      )}

      <div className="space-y-6">
        {step === 1 && (
          <Card
            title="Repository and control point"
            description="Where the code lives, and the last moment KuWarden can refuse."
          >
            {appId && (
              <div className="mb-5">
                <Banner tone="ok">
                  Registered. These fields are settled — the control point can still be moved
                  from the application's own page, where the change is recorded.
                </Banner>
              </div>
            )}
            <fieldset disabled={!!appId} className="grid gap-4 sm:grid-cols-2">
              <Field label="Name" hint="How this application is referred to everywhere else.">
                <Input
                  value={repo.name}
                  placeholder="payments-service"
                  onChange={(e) => setRepo({ ...repo, name: e.target.value })}
                />
              </Field>
              <Field label="Source control">
                <Select
                  value={repo.scm_provider}
                  onChange={(e) =>
                    setRepo({ ...repo, scm_provider: e.target.value as "github" | "azure_repos" })
                  }
                >
                  <option value="github">GitHub</option>
                  <option value="azure_repos">Azure Repos</option>
                </Select>
              </Field>
              <Field
                label="Organisation"
                hint={repo.scm_provider === "github" ? "github.com/<this>/repo" : "dev.azure.com/<this>"}
              >
                <Input
                  value={repo.org}
                  placeholder="acme"
                  onChange={(e) => setRepo({ ...repo, org: e.target.value })}
                />
              </Field>
              <Field label="Repository" hint="Without the .git suffix.">
                <Input
                  value={repo.repo}
                  placeholder="payments-service"
                  onChange={(e) => setRepo({ ...repo, repo: e.target.value })}
                />
              </Field>
              {repo.scm_provider === "azure_repos" && (
                <Field label="Project" hint="Azure Repos nests repositories under a project.">
                  <Input
                    value={repo.project}
                    placeholder="Payments"
                    onChange={(e) => setRepo({ ...repo, project: e.target.value })}
                  />
                </Field>
              )}
              <Field
                label="Control point"
                hint={selectedModel?.note ?? "Declared, never inferred — ADR 0004, invariant 11."}
              >
                <Select
                  value={repo.integration_model}
                  onChange={(e) =>
                    setRepo({ ...repo, integration_model: e.target.value as IntegrationModel })
                  }
                >
                  <option value="">Choose…</option>
                  {MODELS.map((m) => (
                    <option key={m.value} value={m.value}>
                      {m.label}
                    </option>
                  ))}
                </Select>
              </Field>
            </fieldset>

            {/* No Back on step 1: this step *creates* the application, so going back from
                here means leaving — which is what Cancel already does. Returning to it after
                the fact shows what was registered rather than a button that would register a
                second one. */}
            <div className="mt-6">
              <Button
                variant="primary"
                onClick={appId ? () => setStep(2) : createApplication}
                disabled={busy || (!appId && (!repo.name || !repo.org || !repo.repo))}
              >
                {appId ? "Continue" : busy ? "Registering…" : "Register and continue"}
              </Button>
            </div>
          </Card>
        )}

        {step === 2 && (
          <Card
            title="Which tickets are admitted"
            description="Admission control. A ticket that does not match is refused at Triage rather than half-implemented."
          >
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Ticket system">
                <Select
                  value={trigger.provider}
                  onChange={(e) =>
                    setTrigger({
                      ...trigger,
                      provider: e.target.value as "azure_devops" | "jira",
                    })
                  }
                >
                  <option value="azure_devops">Azure DevOps Boards</option>
                  <option value="jira">Jira Cloud</option>
                </Select>
              </Field>

              {isJira ? (
                <>
                  <Field label="Site URL">
                    <Input
                      value={trigger.site}
                      placeholder="https://acme.atlassian.net"
                      onChange={(e) => setTrigger({ ...trigger, site: e.target.value })}
                    />
                  </Field>
                  <Field label="Account email" hint="Jira authenticates as email plus token.">
                    <Input
                      value={trigger.account_email}
                      placeholder="bot@acme.test"
                      onChange={(e) => setTrigger({ ...trigger, account_email: e.target.value })}
                    />
                  </Field>
                </>
              ) : (
                <Field label="Organisation" hint="dev.azure.com/<this>">
                  <Input
                    value={trigger.organisation}
                    placeholder="acme-corp"
                    onChange={(e) => setTrigger({ ...trigger, organisation: e.target.value })}
                  />
                </Field>
              )}

              <Field label="Project" hint={isJira ? "Project key, e.g. PAY" : "Project name"}>
                <Input
                  value={trigger.project}
                  placeholder={isJira ? "PAY" : "Payments"}
                  onChange={(e) => setTrigger({ ...trigger, project: e.target.value })}
                />
              </Field>

              <Field
                label="Required label"
                hint="Leave empty to accept every ticket in the project — a decision, so make it deliberately."
              >
                <Input
                  value={trigger.label}
                  placeholder="kuwarden-auto"
                  onChange={(e) => setTrigger({ ...trigger, label: e.target.value })}
                />
              </Field>

              <Field
                label="Ready state"
                hint="The state a ticket must reach before a run is admitted. A save fires on every field change; a state transition is deliberate."
              >
                <Input
                  value={trigger.ready_state}
                  placeholder="Ready for Agent"
                  onChange={(e) => setTrigger({ ...trigger, ready_state: e.target.value })}
                />
              </Field>

              <Field label="Max story points" hint="Above this, the ticket goes to a human.">
                <Input
                  type="number"
                  value={trigger.max_story_points}
                  onChange={(e) => setTrigger({ ...trigger, max_story_points: e.target.value })}
                />
              </Field>

              {isJira && (
                <Field
                  label="Story points field"
                  hint="The custom field id differs per Jira instance, so there is no default."
                >
                  <Input
                    value={trigger.story_points_field}
                    placeholder="customfield_10016"
                    onChange={(e) =>
                      setTrigger({ ...trigger, story_points_field: e.target.value })
                    }
                  />
                </Field>
              )}
            </div>

            <div className="mt-6 flex gap-2">
              <Button onClick={() => setStep(1)}>Back</Button>
              <Button
                variant="primary"
                onClick={declareTrigger}
                disabled={busy || !trigger.project}
              >
                {busy ? "Saving…" : "Save and continue"}
              </Button>
              <Button onClick={() => setStep(3)}>Skip for now</Button>
            </div>
          </Card>
        )}

        {step === 3 && (
          <Card
            title="Credentials"
            description="Encrypted before they reach the database. No endpoint returns a stored value — they can be replaced or deleted, never read."
          >
            <div className="space-y-5">
              {/* Not `Field`: it stacks label, control and hint inside one element, so a
                  button aligned to its end lines up with the *hint*, several lines below the
                  input it belongs to. The row is built explicitly instead — label above,
                  input and button on one line, hint spanning underneath. */}
              {SLOTS.map((slot) => (
                <div key={slot.kind}>
                  <label
                    htmlFor={`cred-${slot.kind}`}
                    className="mb-1.5 block text-[12px] font-medium text-muted"
                  >
                    {slot.label}
                    {!slot.required && " — optional"}
                    {stored.includes(slot.kind) && (
                      <span className="ml-2 text-emerald-600 dark:text-emerald-400">
                        ● stored
                      </span>
                    )}
                  </label>
                  <div className="flex items-center gap-3">
                    <div className="min-w-0 flex-1">
                      <Input
                        id={`cred-${slot.kind}`}
                        type="password"
                        autoComplete="off"
                        placeholder={
                          stored.includes(slot.kind)
                            ? "stored — paste a new value to replace it"
                            : "paste the token"
                        }
                        value={secrets[slot.kind] ?? ""}
                        onChange={(e) =>
                          setSecrets({ ...secrets, [slot.kind]: e.target.value })
                        }
                      />
                    </div>
                    <Button
                      onClick={() => storeSecret(slot.kind)}
                      disabled={busy || !secrets[slot.kind]}
                    >
                      {stored.includes(slot.kind) ? "Replace" : "Store"}
                    </Button>
                  </div>
                  <p className="mt-1.5 text-[12px] leading-relaxed text-faint">{slot.hint}</p>
                </div>
              ))}
            </div>

            <div className="mt-6 flex items-center gap-3">
              <Button onClick={() => setStep(2)}>Back</Button>
              <Button variant="primary" onClick={() => setStep(4)}>
                Continue
              </Button>
              {missing.length > 0 && (
                <span className="text-[12px] text-amber-700 dark:text-amber-400">
                  {missing.length} required slot(s) still empty — a run will fail at the first
                  node that needs one.
                </span>
              )}
            </div>
          </Card>
        )}

        {step === 4 && (
          <Card
            title="Check the connections"
            description="Read-only. Each platform is reported separately — a working SCM token with a broken ticket token is the most common half-configured state."
            actions={
              <Button onClick={runChecks} disabled={busy}>
                {busy ? "Checking…" : "Test connections"}
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

            <div className="mt-6 flex gap-2">
              <Button onClick={() => setStep(3)}>Back</Button>
              <Button
                variant="primary"
                onClick={() => navigate(`/applications/${appId}`)}
                disabled={!appId}
              >
                Done — open the application
              </Button>
            </div>
          </Card>
        )}
      </div>
    </div>
  );
}

/**
 * The choice on the way out of a half-finished registration.
 *
 * Both outcomes are offered because both are legitimate. It states what exists rather than
 * asking "are you sure?" about an unnamed thing — the count of stored credentials is the part
 * an operator will not otherwise remember, and it is the part that cannot be recovered.
 */
function Leaving({
  name,
  stored,
  busy,
  error,
  onDiscard,
  onKeep,
  onStay,
}: {
  name: string;
  stored: number;
  busy: boolean;
  error: string | null;
  onDiscard: () => void;
  onKeep: () => void;
  onStay: () => void;
}) {
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Leave this registration"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      // Clicking the backdrop is the safe outcome, never the destructive one.
      onClick={onStay}
    >
      <div
        className="w-full max-w-md rounded-2xl border border-line bg-surface p-6 shadow-lg"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="text-[15px] font-semibold">Leave this registration?</h2>
        <p className="mt-2 text-[13px] leading-relaxed text-muted">
          <span className="font-medium text-ink">{name || "The application"}</span> is already
          registered. It can stay as it is and be finished later — the list marks it as
          unconfigured until it can run.
        </p>
        {stored > 0 && (
          <p className="mt-2 text-[13px] leading-relaxed text-muted">
            Discarding also destroys {stored} stored credential{stored === 1 ? "" : "s"}. No
            endpoint returns one, so they would have to be pasted again.
          </p>
        )}

        {error && (
          <div className="mt-4">
            <Banner tone="error">{error}</Banner>
          </div>
        )}

        <div className="mt-6 flex flex-wrap justify-end gap-2">
          <Button onClick={onStay} disabled={busy}>
            Stay
          </Button>
          <Button variant="danger" onClick={onDiscard} disabled={busy}>
            {busy ? "Discarding…" : "Discard it"}
          </Button>
          <Button variant="primary" onClick={onKeep} disabled={busy}>
            Keep and finish later
          </Button>
        </div>
      </div>
    </div>
  );
}

function Steps({ current, onGo }: { current: Step; onGo: (step: Step) => void }) {
  const labels = ["Repository", "Ticketing", "Credentials", "Check"];
  return (
    <ol className="mb-6 flex flex-wrap gap-x-2 gap-y-2 text-[12px]">
      {labels.map((label, i) => {
        const n = (i + 1) as Step;
        const done = n < current;
        return (
          <li key={label} className="flex items-center gap-2">
            <button
              type="button"
              // Only backwards. Jumping ahead would skip a step that commits something.
              onClick={() => done && onGo(n)}
              disabled={!done}
              className={`flex size-5 items-center justify-center rounded-full text-[11px] font-medium ${
                done ? "cursor-pointer" : "cursor-default"
              } ${
                n === current
                  ? "bg-accent text-white"
                  : done
                    ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300"
                    : "ring-1 ring-line text-muted"
              }`}
            >
              {done ? "✓" : n}
            </button>
            <span className={n === current ? "font-medium" : "text-muted"}>{label}</span>
            {i < labels.length - 1 && <span className="px-1 text-faint">→</span>}
          </li>
        );
      })}
    </ol>
  );
}
