-- 007 — per-application configuration, so one worker can serve many applications.
--
-- Until now the worker loaded a single `kuwarden.yaml` from its own filesystem at startup and
-- handed the same AppConfig to every node, whichever application the run belonged to.
-- Credentials were already per application; configuration was not. Registering a second
-- application therefore gave it its own tokens pointed at the first one's repository.
--
-- The YAML is stored verbatim rather than shredded into columns. One parser (`engine.config`)
-- with one set of validation rules is the point: a second, column-shaped representation would
-- drift from the file format the schema is documented as, and applications would behave
-- differently depending on which path their configuration arrived by.
--
-- This is a step, not the destination. The file is designed to live in the application's own
-- repository and be reviewed as a pull request there — which is why it holds no credentials
-- and is a protected path. Reading it per run from that repository needs `policy.yaml` first,
-- because until an operator-owned policy layer exists a team could grant itself auto-merge in
-- its own PR.
CREATE TABLE app_config (
    app_id     UUID PRIMARY KEY REFERENCES app_registry(id) ON DELETE CASCADE,
    yaml       TEXT        NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Who stored it. Not an audit trail — `flow_events` is that — but enough to answer "who
    -- changed what this application is allowed to do" without reading the whole journal.
    updated_by TEXT        NOT NULL
);

COMMENT ON TABLE app_config IS
    'One application''s kuwarden.yaml, stored verbatim. Parsed by engine.config.parse on read.';
