/**
 * The API client layer.
 *
 * Every network call in the Workbench goes through this file. CLAUDE.md forbids `fetch`
 * anywhere else, which keeps one place to add auth headers, one place to handle errors, and
 * one place to audit for "does anything here send a credential somewhere unexpected".
 */

import type {
  Application,
  CredentialState,
  Evidence,
  IntegrationModel,
  ProbeResult,
  Run,
  RunEvent,
  Principal,
  Role,
  SandboxStatus,
  Trigger,
  User,
} from "./types";

/** Raised for any non-2xx response, carrying the API's own message where there is one. */
export class ApiError extends Error {
  // Declared explicitly rather than as a constructor parameter property: the tsconfig sets
  // `erasableSyntaxOnly`, which rejects syntax that cannot be erased by a type-stripping
  // transform.
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/**
 * One request. Returns `undefined` for 204, which several endpoints use deliberately —
 * storing a credential returns no body because there is no safe body to return.
 */
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });

  if (response.status === 204) return undefined as T;

  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    // A session that lapsed mid-visit used to surface as whatever the calling component
    // happened to render for an error — a line of small grey text in a corner, which reads
    // as "this feature is broken" rather than "you are signed out". Announced once, here,
    // so every caller gets the same behaviour and none of them has to remember.
    if (response.status === 401) window.dispatchEvent(new Event("kuwarden:signed-out"));
    const detail =
      typeof body === "object" && body !== null && "detail" in body
        ? String((body as { detail: unknown }).detail)
        : `request failed (${response.status})`;
    throw new ApiError(detail, response.status);
  }
  return body as T;
}

export const api = {
  health: () => request<{ status: string }>("/api/health"),

  // --- session and users --------------------------------------------------------------

  /** Whether any account exists. The only endpoint that does not require one. */
  bootstrapState: () => request<{ configured: boolean }>("/api/bootstrap"),

  signIn: (email: string, password: string) =>
    request<Principal>("/api/session", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),

  signOut: () => request<void>("/api/session", { method: "DELETE" }),

  /** Resolve the current session. 401 when there is none — that is the signal, not an error. */
  whoami: () => request<Principal>("/api/session"),

  listUsers: () => request<User[]>("/api/users"),

  addUser: (body: {
    email: string;
    display_name: string;
    password: string;
    role: Role;
  }) =>
    request<{ id: string }>("/api/users", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  /** Disable an account. Ends its sessions immediately rather than at cookie expiry. */
  disableUser: (userId: string) =>
    request<void>(`/api/users/${userId}/disable`, { method: "POST" }),

  /** What the sandbox host enforces. Probed, not assumed. */
  sandboxStatus: () => request<SandboxStatus>("/api/sandbox"),

  // --- applications -----------------------------------------------------------------------

  listApplications: () => request<Application[]>("/api/applications"),

  registerApplication: (body: {
    name: string;
    scm_provider: "github" | "azure_repos";
    org: string;
    repo: string;
    project: string | null;
    integration_model: IntegrationModel;
  }) =>
    request<Application>("/api/applications", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  deleteApplication: (id: string) =>
    request<void>(`/api/applications/${id}`, { method: "DELETE" }),

  // --- triggers ---------------------------------------------------------------------------

  listTriggers: (appId: string) =>
    request<Trigger[]>(`/api/applications/${appId}/triggers`),

  declareTrigger: (
    appId: string,
    body: Omit<Trigger, "id">,
  ) =>
    request<{ id: string }>(`/api/applications/${appId}/triggers`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  removeTrigger: (appId: string, triggerId: string) =>
    request<void>(`/api/applications/${appId}/triggers/${triggerId}`, {
      method: "DELETE",
    }),

  // --- credentials ------------------------------------------------------------------------

  listCredentials: (appId: string) =>
    request<CredentialState>(`/api/applications/${appId}/credentials`),

  /**
   * Store a credential. Write-only by design: there is no corresponding read, and the value
   * is never echoed back — not in the response, not in an error message.
   */
  storeCredential: (appId: string, kind: string, value: string) =>
    request<void>(`/api/applications/${appId}/credentials/${kind}`, {
      method: "PUT",
      body: JSON.stringify({ value }),
    }),

  forgetCredential: (appId: string, kind: string) =>
    request<void>(`/api/applications/${appId}/credentials/${kind}`, {
      method: "DELETE",
    }),

  /** Hand a ticket to the Flow Engine. Approver role — starting work is operational. */
  startRun: (appId: string, ticketId: string) =>
    request<{ run_id: string; workflow_id: string; started_by: string }>(
      `/api/applications/${appId}/runs`,
      { method: "POST", body: JSON.stringify({ ticket_id: ticketId }) },
    ),

  /**
   * Can the stored credentials reach each platform? Separate from `probe`, which answers the
   * governance question. Conflating them is why a governance verdict once read as a broken
   * token.
   */
  checkConnections: (appId: string) =>
    request<Record<string, { ok: boolean; target?: string; detail: string }>>(
      `/api/applications/${appId}/check`,
      { method: "POST" },
    ),

  /** Ask the platform what it can actually do, and whether the declared model is achievable. */
  probe: (appId: string) =>
    request<ProbeResult>(`/api/applications/${appId}/probe`, { method: "POST" }),

  /**
   * Move the control point. The reason is required, not decoration: the change is written to
   * an append-only log, and a log full of blank reasons is a list of timestamps.
   */
  changeControlPoint: (appId: string, integrationModel: string, reason: string) =>
    request<{ integration_model: string; changed: boolean; runs_predating_this_change?: number }>(
      `/api/applications/${appId}/control-point`,
      { method: "PATCH", body: JSON.stringify({ integration_model: integrationModel, reason }) },
    ),

  // --- runs -------------------------------------------------------------------------------

  listRuns: () => request<Run[]>("/api/runs"),

  listRunEvents: (runId: string) => request<RunEvent[]>(`/api/runs/${runId}/events`),

  /**
   * Per-attempt execution detail from Temporal's history — stack traces included.
   *
   * Deliberately not part of the audit trail: `flow_events` is the permanent record and a
   * stack trace cannot be removed from it once written. This is diagnosis, retained on
   * Temporal's schedule, and 404s once that history expires.
   */
  runDiagnostics: (runId: string) =>
    request<
      {
        node_id: string;
        outcome: string;
        error: string;
        message: string;
        stack_trace: string;
        at: string;
      }[]
    >(`/api/runs/${runId}/diagnostics`),

  /** What an approver is shown, plus the digest their decision will be bound to. */
  runEvidence: (runId: string) => request<Evidence>(`/api/runs/${runId}/evidence`),

  /**
   * Record a decision. `digest` must be the one that came back with the evidence being
   * displayed — the server refuses with 409 if the run has produced new evidence since.
   */
  decide: (runId: string, approved: boolean, digest: string, comment: string) =>
    request<{ recorded: boolean; approved: boolean; principal: string }>(
      `/api/runs/${runId}/approval`,
      {
        method: "POST",
        body: JSON.stringify({ approved, evidence_digest: digest, comment }),
      },
    ),
};
