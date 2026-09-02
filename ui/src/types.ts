/**
 * Shapes returned by the Workbench API.
 *
 * Deliberately mirrors the engine's own vocabulary — `risk_tier`, `control_mode`,
 * `integration_model` — rather than inventing UI-side synonyms. docs/GLOSSARY.md fixes this
 * vocabulary, and terminology drift in this project has already caused one design error.
 */

/** Where the control point sits. Declared per application, never inferred — ADR 0004. */
export type IntegrationModel =
  | "kuwarden_deploys"
  | "gated_merge"
  | "gated_deployment";

/** How much verification and human approval a change receives — ADR 0002. */
export type RiskTier = "low" | "medium" | "high";

export type RunStatus =
  | "running"
  | "suspended"
  | "succeeded"
  | "rejected"
  | "failed"
  | "aborted"
  // Stopped by a person, from the Workbench — the only status not written by the workflow.
  // Distinct from `aborted`, which is the flow stopping itself on the evidence. Implies
  // compensation did NOT run, so a branch may still exist on the remote.
  | "terminated";

export interface Application {
  id: string;
  name: string;
  repo_url: string;
  integration_model: IntegrationModel;
  created_at: string;
}

/** Which tickets an application accepts. Mirrors the `triggers` block of kuwarden.yaml. */
export interface Trigger {
  id: string;
  provider: "jira" | "azure_devops";
  project: string;
  site: string | null;
  account_email: string | null;
  organisation: string | null;
  /** null means every ticket in the project qualifies — a decision, not an omission. */
  label: string | null;
  /**
   * The workflow state that means "go". null means state is not checked. A ticket *save*
   * fires on every field change; a state transition is deliberate, which is the difference
   * between reading an intention and inferring one.
   */
  ready_state: string | null;
  max_story_points: number | null;
  /** Jira custom field id. Differs per instance, so there is no default. */
  story_points_field: string | null;
}

export interface CredentialState {
  /** Which credentials are stored. Values are never returned by any endpoint. */
  present: string[];
  supported: string[];
}

export interface ProbeResult {
  declared: IntegrationModel;
  /** False when the platform cannot support the declared model. Registration should stop. */
  achievable: boolean;
  reason: string;
  capabilities: {
    deployment_protection: boolean;
    required_status_checks: boolean;
    restrictable_pipeline_triggers: boolean;
    detail: Record<string, string>;
  };
}

export interface Run {
  id: string;
  /** Which application this run belongs to — what a re-run needs. */
  app_id: string;
  /** Joined from app_registry. `(deleted)` if the application is gone but the run remains. */
  app_name: string;
  ticket_system: string;
  ticket_id: string;
  risk_tier: RiskTier;
  status: RunStatus;
  policy_commit: string;
  created_at: string;
  /** Null while the run is still in flight. */
  ended_at: string | null;
}

export interface RunEvent {
  seq: number;
  kind: string;
  node_id: string | null;
  /**
   * `authorized` means KuWarden gated the effect; `observed` means we watched it happen.
   * `null` means the event represents no external effect at all — never "we did not check".
   * Invariant 11.
   */
  control_mode: "authorized" | "observed" | null;
  /** Event-specific detail. Self-describing on purpose, so reading an old record needs
      nothing but the record. */
  payload: Record<string, unknown>;
  occurred_at: string;
}

/**
 * A node's account of what it read, decided and produced — `engine.nodes.notes`.
 *
 * Carried in the `payload` of `node_completed` and `verifier_verdict`. Optional everywhere,
 * because runs recorded before notes existed have none and must still render.
 */
export interface NoteSection {
  title: string;
  kind: "fields" | "checks" | "text";
  /** `fields`: [label, value] pairs, already stringified engine-side. */
  rows?: [string, string][] | NoteCheck[];
  /** `text` only. */
  body?: string;
  /** Written by whoever filed the ticket, or returned by a model — never by KuWarden. */
  untrusted?: boolean;
  truncated?: boolean;
  /** Which end of an over-long block was kept. Test output keeps the end. */
  kept?: "start" | "end";
  full_length?: number;
}

export interface NoteCheck {
  label: string;
  required: string;
  found: string;
  ok: boolean;
}

export interface Notes {
  summary: string;
  sections: NoteSection[];
}

/**
 * What the sandbox host actually enforces.
 *
 * Probed by running a container, not by asking the runtime: rootless podman on a cgroups v1
 * host accepts `--memory` and silently ignores it, so "what was requested" and "what is
 * applied" are different questions.
 */
export interface SandboxStatus {
  available: boolean;
  fully_enforced: boolean;
  /** Human-readable list of bounds that are *not* applied. Empty when nothing is missing. */
  gaps: string[];
  reason?: string;
  enforced?: Record<string, boolean>;
}

/** ADR 0003 §1. Ordered: each role includes the ones before it. */
export type Role = "viewer" | "approver" | "admin";

/** Who is signed in. The start of ADR 0003's delegation chain. */
export interface Principal {
  id: string;
  email: string;
  display_name: string;
  role: Role;
}

export interface User extends Principal {
  disabled_at: string | null;
  created_at: string;
  last_login_at: string | null;
}

/**
 * One ticket's evidence graph — ADR 0012. Recorded facts, never inferred: every node is a row
 * and every edge is a foreign key, a commit trailer, or git's own numstat.
 */
export interface TicketGraph {
  run_id: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
  node_count: number;
  edge_count: number;
}

export interface GraphNode {
  id: string;
  kind: "ticket" | "run" | "file";
  label: string;
  /** `run` only. */
  status: string;
  /** `run` only. The work-item revision a service hook launched it for, when it was one. */
  revision: string | null;
  /** `run` only. Every push, in order; the first carries the base it branched from. */
  pushes: { commit: string; base: string; attempt: number }[];
  /** `run` only. True for the run being viewed. */
  self: boolean;
}

/** One run that changed a given file — the reverse query behind ADR 0012. */
export interface FileRun {
  run_id: string;
  path: string;
  added: number;
  removed: number;
  ticket_system: string;
  ticket_id: string;
  status: string;
  app_name: string;
  created_at: string;
}

export interface GraphEdge {
  from: string;
  to: string;
  kind: "asked" | "spawned" | "changed";
  /** `changed` only, from git. */
  added?: number;
  removed?: number;
}

/**
 * The evidence document an approver decides against, and the digest that binds them to it.
 *
 * `digest` is opaque here on purpose — the UI never recomputes it. Two implementations of
 * "canonical form" drift, and the symptom would be approvals bouncing for no visible reason.
 * The server computes it, the UI echoes it back.
 */
export interface Evidence {
  digest: string;
  document: {
    schema: number;
    run_id: string;
    application: string | null;
    ticket: { system: string; id: string };
    /** Authoritative — decided over the actual diff, after the Coder loop. */
    risk_tier: string;
    /** What intake guessed from the ticket alone, before any code existed. */
    provisional_risk_tier: string;
    /** Which rule settled the authoritative tier, in the words the rule is written in. */
    risk_tier_reason: string;
    status: string;
    policy_commit: string;
    policy_bundle: Record<string, unknown>;
    started_at: string;
    /** Empty when no verdict was recorded. `source` says who ran the tests, not just that they ran. */
    tests: { exit_code?: number; source?: string; duration_ms?: number; url?: string | null };
    sandbox_isolation: { state?: string; gaps?: string[] };
    /**
     * What each verifier wrote, not how many passed. A passing verdict is not an empty one —
     * ticket 50 shipped with `correctness` passing and its own findings saying the feature
     * was not implemented.
     */
    verifications: {
      verifier: string;
      blocks: boolean;
      findings: string[];
      /** Structured. `blocks` above is `graded.some(f => f.severity === "blocking")`. */
      graded?: { detail: string; severity: "blocking" | "advisory" | "note" }[];
    }[];
    /**
     * A running deployment of this exact commit, when the platform published one. Empty is the
     * ordinary case and carries its own caveat — a link, never a verdict.
     */
    preview_url: string;
    /** Everything about this evidence that is weaker than it looks. Rendered above the controls. */
    caveats: string[];
    events: RunEvent[];
  };
}
