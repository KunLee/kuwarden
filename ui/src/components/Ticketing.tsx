/**
 * Which tickets an application accepts.
 *
 * This is admission control, not a filter for convenience: a ticket that does not match is
 * refused at intake, before any model has read it. Getting it wrong in the permissive
 * direction means an agent starts work on tickets nobody intended it to touch.
 */

import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import type { Trigger } from "../types";
import { useCan } from "../auth";
import { Banner, Button, Card, Field, Input, Select } from "./ui";

type Draft = Omit<Trigger, "id">;

const EMPTY: Draft = {
  provider: "jira",
  project: "",
  site: "",
  account_email: "",
  organisation: "",
  label: "",
  ready_state: "",
  max_story_points: null,
  story_points_field: "",
};

export function Ticketing({ appId }: { appId: string }) {
  const canAdmin = useCan("admin");
  const [triggers, setTriggers] = useState<Trigger[]>([]);
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [error, setError] = useState<string | null>(null);
  //: Non-null while amending an existing rule rather than declaring a new one. The form is
  //: the same either way; what changes is whether identity fields may be touched.
  const [editing, setEditing] = useState<string | null>(null);

  async function refresh() {
    try {
      setTriggers(await api.listTriggers(appId));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }

  useEffect(() => {
    void refresh();
  }, [appId]);

  function edit(trigger: Trigger) {
    setEditing(trigger.id);
    const { id: _id, ...rest } = trigger;
    setDraft({ ...rest });
    setError(null);
  }

  function cancel() {
    setEditing(null);
    setDraft(EMPTY);
    setError(null);
  }

  async function amend(triggerId: string) {
    setError(null);
    try {
      // Only the admission rules. Provider, organisation and project decide which board this
      // rule governs, and the server refuses to amend them for that reason.
      await api.amendTrigger(appId, triggerId, {
        label: draft.label || null,
        ready_state: draft.ready_state || null,
        max_story_points: draft.max_story_points,
        story_points_field: draft.story_points_field || null,
      });
      cancel();
      await refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }

  async function declare() {
    setError(null);
    try {
      // Empty strings are sent as null so "not configured" and "configured as blank" stay
      // distinguishable in the database.
      await api.declareTrigger(appId, {
        ...draft,
        site: draft.site || null,
        account_email: draft.account_email || null,
        organisation: draft.organisation || null,
        label: draft.label || null,
        ready_state: draft.ready_state || null,
        story_points_field: draft.story_points_field || null,
      });
      setDraft(EMPTY);
      await refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }

  const isJira = draft.provider === "jira";

  return (
    <Card
      title="Ticketing"
      description="Which tickets this application accepts. A ticket that does not match is refused at intake."
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Provider">
          <Select
            value={draft.provider}
            disabled={editing !== null}
            onChange={(e) =>
              setDraft({ ...draft, provider: e.target.value as Draft["provider"] })
            }
          >
            <option value="jira">Jira Cloud</option>
            <option value="azure_devops">Azure DevOps Boards</option>
          </Select>
        </Field>

        {isJira ? (
          <>
            <Field label="Site URL">
              <Input
                value={draft.site ?? ""}
                placeholder="https://acme.atlassian.net"
                onChange={(e) => setDraft({ ...draft, site: e.target.value })}
              disabled={editing !== null}
              />
            </Field>
            <Field
              label="Account email"
              hint="Jira authenticates the API token against this account."
            >
              <Input
                value={draft.account_email ?? ""}
                placeholder="bot@acme.test"
                onChange={(e) => setDraft({ ...draft, account_email: e.target.value })}
              disabled={editing !== null}
              />
            </Field>
          </>
        ) : (
          <Field label="Organisation">
            <Input
              value={draft.organisation ?? ""}
              placeholder="acme"
              onChange={(e) => setDraft({ ...draft, organisation: e.target.value })}
            disabled={editing !== null}
            />
          </Field>
        )}

        <Field label="Project" hint={isJira ? "Project key, e.g. PAY" : "Project name"}>
          <Input
            value={draft.project}
            placeholder={isJira ? "PAY" : "Payments"}
            onChange={(e) => setDraft({ ...draft, project: e.target.value })}
            disabled={editing !== null}
          />
        </Field>

        <Field
          label="Required label"
          hint="Leave empty to accept every ticket in the project — say so deliberately."
        >
          <Input
            value={draft.label ?? ""}
            placeholder="kuwarden-auto"
            onChange={(e) => setDraft({ ...draft, label: e.target.value })}
          />
        </Field>

        <Field
          label="Ready state"
          hint="The state a ticket must be in before a run is admitted. Leave empty to accept any state — but then every save is a candidate, which is how a webhook becomes expensive."
        >
          <Input
            value={draft.ready_state ?? ""}
            placeholder="Ready for Agent"
            onChange={(e) => setDraft({ ...draft, ready_state: e.target.value })}
          />
        </Field>

        <Field label="Max story points" hint="Above this, the ticket goes to a human.">
          <Input
            type="number"
            value={draft.max_story_points ?? ""}
            placeholder="5"
            onChange={(e) =>
              setDraft({
                ...draft,
                max_story_points: e.target.value ? Number(e.target.value) : null,
              })
            }
          />
        </Field>

        {isJira && (
          <Field
            label="Story points field"
            hint="Jira stores this in a custom field whose id differs per instance. Without it, story points are ignored."
          >
            <Input
              value={draft.story_points_field ?? ""}
              placeholder="customfield_10016"
              onChange={(e) => setDraft({ ...draft, story_points_field: e.target.value })}
            />
          </Field>
        )}
      </div>

      {error && (
        <div className="mt-4">
          <Banner tone="error">{error}</Banner>
        </div>
      )}

      <div className="mt-4 flex items-center gap-3">
        {editing ? (
          <>
            <Button variant="primary" onClick={() => amend(editing)} disabled={!canAdmin}>
              Save changes
            </Button>
            <Button onClick={cancel}>Cancel</Button>
          </>
        ) : (
          <Button variant="primary" onClick={declare} disabled={!canAdmin || !draft.project}>
            Add trigger
          </Button>
        )}
      </div>

      {triggers.length > 0 && (
        <table className="mt-5 w-full text-sm">
          <thead className="text-xs text-muted">
            <tr className="border-b border-line">
              <th className="pb-2 text-left font-medium">Provider</th>
              <th className="pb-2 text-left font-medium">Project</th>
              <th className="pb-2 text-left font-medium">Admits</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {triggers.map((trigger) => (
              <tr key={trigger.id} className="border-b border-line last:border-0">
                <td className="mono py-2 text-xs">{trigger.provider}</td>
                <td className="py-2">{trigger.project}</td>
                <td className="py-2 text-xs text-muted">
                  {trigger.label ? (
                    <>
                      label <span className="mono">{trigger.label}</span>
                    </>
                  ) : (
                    // Stated rather than shown as a blank, because "every ticket" is a
                    // materially different posture from "not configured yet".
                    <span className="text-amber-700 dark:text-amber-400">
                      every ticket in the project
                    </span>
                  )}
                  {trigger.ready_state ? (
                    <>
                      , state <span className="mono">{trigger.ready_state}</span>
                    </>
                  ) : (
                    // Same reasoning as the label: "any state" is a posture, not a blank.
                    <span className="text-amber-700 dark:text-amber-400">, any state</span>
                  )}
                  {trigger.max_story_points !== null && `, ≤ ${trigger.max_story_points} points`}
                </td>
                <td className="py-2 text-right">
                  {canAdmin && (
                    <span className="flex justify-end gap-3">
                      {/* Amending beats delete-and-recreate: while no trigger exists the
                          application accepts no work at all, and changing one field should
                          not open that window. */}
                      <button
                        onClick={() => edit(trigger)}
                        className="text-xs text-accent hover:underline"
                      >
                        Edit
                      </button>
                      <button
                        onClick={async () => {
                          await api.removeTrigger(appId, trigger.id);
                          if (editing === trigger.id) cancel();
                          await refresh();
                        }}
                        className="text-xs text-red-600 hover:underline dark:text-red-400"
                      >
                        Remove
                      </button>
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}
