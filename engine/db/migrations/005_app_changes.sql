-- 005 — an append-only record of configuration changes to a registered application.
--
-- The control point (`integration_model`) was previously unchangeable in practice: there is
-- no update endpoint, and `DELETE` is refused once any run references the application. That
-- immutability was not a decision — it fell out of two unrelated ones — and it left an
-- operator who mistyped the control point with no remedy but registering a second
-- application.
--
-- Making it changeable without a record would be worse than leaving it stuck. `flow_runs`
-- does not store which integration model governed a run, so changing the application's model
-- silently re-interprets every past run under a control point that was not in force at the
-- time. That is the same failure `policy_commit` pinning exists to prevent (ADR 0003 §4), and
-- the eventual fix is to pin the effective configuration into each run. Until that exists,
-- this table is the compensating control: the change itself is evidence, and it cannot be
-- edited away.
--
-- Append-only for the same reason `flow_events` is: a record an administrator can rewrite is
-- a record of nothing.

CREATE TABLE app_changes (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    app_id       UUID NOT NULL REFERENCES app_registry(id) ON DELETE CASCADE,

    -- Which column changed. Free text rather than an enum: this table should accept a new
    -- field without a migration, because the alternative is that somebody changes a field
    -- without recording it rather than write one.
    field        TEXT NOT NULL,
    old_value    TEXT,
    new_value    TEXT NOT NULL,

    -- The authenticated principal, never a value the client supplied. An "who changed the
    -- control point" answer that the changer could choose is not an answer.
    changed_by   TEXT NOT NULL,
    reason       TEXT NOT NULL DEFAULT '',
    changed_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX app_changes_by_app ON app_changes (app_id, changed_at DESC);

CREATE FUNCTION app_changes_append_only() RETURNS TRIGGER AS $$
BEGIN
    -- One exception, and it is narrow: the cascade from deleting the application itself.
    -- During a cascade the parent row is already gone, which is what distinguishes it from
    -- somebody deleting a change row directly. The API refuses to delete an application while
    -- any run references it, so by the time this fires there is no run whose interpretation
    -- this history protects — it is evidence about nothing. A direct DELETE, with the
    -- application still present, is someone editing history and is refused.
    IF TG_OP = 'DELETE' AND NOT EXISTS (SELECT 1 FROM app_registry WHERE id = OLD.app_id) THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'app_changes is append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER app_changes_no_update BEFORE UPDATE OR DELETE ON app_changes
    FOR EACH ROW EXECUTE FUNCTION app_changes_append_only();
