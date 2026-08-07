-- 001 — the run tree and the audit trail.
--
-- Assembles the fragments in ADR 0002 §"Recursive composition", ADR 0003 §4 and ADR 0004 §3
-- into one schema. The tree columns are here from the first migration because audit data is
-- append-only by definition and therefore cannot be migrated freely later.

CREATE TABLE app_registry (
    id                UUID PRIMARY KEY,
    name              TEXT NOT NULL UNIQUE,
    repo_url          TEXT NOT NULL,
    -- No default, deliberately. Which control point governs a deployment is a governance
    -- decision, not an inference — ADR 0004. The adapter may validate the declaration; it
    -- may not make it.
    integration_model TEXT NOT NULL
        CHECK (integration_model IN ('kuwarden_deploys', 'gated_merge', 'gated_deployment')),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE flow_runs (
    id             UUID PRIMARY KEY,
    parent_run_id  UUID REFERENCES flow_runs(id),          -- NULL for a root run
    root_run_id    UUID NOT NULL REFERENCES flow_runs(id),
    app_id         UUID NOT NULL REFERENCES app_registry(id),
    workflow_id    TEXT NOT NULL,
    ticket_system  TEXT NOT NULL,
    ticket_id      TEXT NOT NULL,
    risk_tier      TEXT NOT NULL CHECK (risk_tier IN ('low', 'medium', 'high')),
    status         TEXT NOT NULL CHECK (status IN ('running', 'suspended', 'succeeded',
                                                   'rejected', 'failed', 'aborted')),
    schema_version INT  NOT NULL,
    -- Both are kept: the SHA for provenance, the bundle so the record is self-describing
    -- when the policy repository is unavailable or its history has been rewritten.
    policy_commit  TEXT  NOT NULL,
    policy_bundle  JSONB NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at       TIMESTAMPTZ
);

CREATE INDEX flow_runs_root_idx   ON flow_runs (root_run_id);
CREATE INDEX flow_runs_parent_idx ON flow_runs (parent_run_id);

-- The audit trail. `flow_runs.status` is a mutable convenience; this is the record.
CREATE TABLE flow_events (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id       UUID NOT NULL REFERENCES flow_runs(id),
    seq          INT  NOT NULL,
    kind         TEXT NOT NULL,
    node_id      TEXT,
    payload      JSONB NOT NULL DEFAULT '{}'::jsonb,

    -- ADR 0004 specifies this NOT NULL. Applied literally to every row it would force a
    -- value onto events that represent no external effect at all -- a node starting, a gate
    -- opening -- and inventing one there is precisely the defaulting invariant 11 forbids.
    -- So: required on exactly the events that touch the outside world, forbidden elsewhere.
    -- Same guarantee, without manufacturing evidence for rows that have none.
    control_mode TEXT CHECK (control_mode IN ('authorized', 'observed')),

    occurred_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (run_id, seq),
    CONSTRAINT control_mode_exactly_on_effects
        CHECK ((kind = 'external_effect') = (control_mode IS NOT NULL))
);

CREATE INDEX flow_events_run_idx ON flow_events (run_id, seq);

-- Invariant 9 — the audit trail is append-only. Enforced in the database rather than
-- trusted to every future caller.
CREATE FUNCTION flow_events_append_only() RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'flow_events is append-only (invariant 9); attempted %', TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER flow_events_no_update BEFORE UPDATE OR DELETE ON flow_events
    FOR EACH ROW EXECUTE FUNCTION flow_events_append_only();

-- ADR 0003: policy_bundle is written once, at run start, and never updated. The run's
-- position in the tree is equally fixed once it exists.
CREATE FUNCTION flow_runs_immutable_columns() RETURNS TRIGGER AS $$
BEGIN
    IF NEW.policy_commit IS DISTINCT FROM OLD.policy_commit
       OR NEW.policy_bundle IS DISTINCT FROM OLD.policy_bundle
       OR NEW.parent_run_id IS DISTINCT FROM OLD.parent_run_id
       OR NEW.root_run_id   IS DISTINCT FROM OLD.root_run_id THEN
        RAISE EXCEPTION 'flow_runs.% is immutable after run start', 'policy/lineage columns';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER flow_runs_pin_immutable BEFORE UPDATE ON flow_runs
    FOR EACH ROW EXECUTE FUNCTION flow_runs_immutable_columns();
