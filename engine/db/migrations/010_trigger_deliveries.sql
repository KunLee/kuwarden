-- 010 — every webhook delivery, recorded where the hook itself can write it.
--
-- Two problems, one row.
--
-- **Trigger health was invisible.** A dead subscription produces silence, and silence is
-- indistinguishable from nobody filing a ticket. The quick tunnel this project develops behind
-- mints a new hostname on every restart, so the subscription has gone stale more than once, and
-- every time the answer took a trip through the Azure DevOps subscription history to find. The
-- record of "did anything arrive, and what did we say about it" lived nowhere.
--
-- **And the supersession guard was blind exactly when it mattered.** It refuses a delivery whose
-- revision is not newer than the highest already launched for that work item — read from
-- `flow_runs`. But `flow_runs` is written by an activity, so when the worker is broken no row is
-- ever written, the guard sees nothing, and every replayed revision starts a workflow. That is
-- not hypothetical: work item 52 arrived as r2, r4 and r6 while the worker could not execute a
-- workflow task, all three were admitted, and all three sat in Temporal doing nothing.
--
-- Writing the delivery here, from the hook, before the workflow is started, fixes both. The
-- record does not depend on any downstream component being alive, which is precisely the
-- property the old one lacked.
CREATE TABLE trigger_deliveries (
    id           BIGSERIAL PRIMARY KEY,
    app_id       UUID        NOT NULL REFERENCES app_registry(id) ON DELETE CASCADE,
    provider     TEXT        NOT NULL,
    -- The work item, as the provider names it. TEXT because Jira keys are not integers.
    work_item    TEXT        NOT NULL,
    -- Zero when the payload carried no revision. Not NULL: "we did not look" and "there was
    -- none" are different facts and only one of them is true here.
    revision     INT         NOT NULL,
    -- What the endpoint decided, in the words it returned to the caller. A delivery that was
    -- refused is as much a fact as one that started a run, and the reason is the whole value:
    -- "does not carry the 'kuwarden-auto' tag" ends an investigation that otherwise starts by
    -- asking whether the webhook is even connected.
    started      BOOLEAN     NOT NULL,
    reason       TEXT,
    run_id       UUID        REFERENCES flow_runs(id),
    received_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The supersession guard's query: the highest revision seen for one work item.
CREATE INDEX trigger_deliveries_item_idx
    ON trigger_deliveries (app_id, work_item, revision DESC);

-- Trigger health: the most recent delivery for an application, whatever it was for.
CREATE INDEX trigger_deliveries_recent_idx
    ON trigger_deliveries (app_id, received_at DESC);

COMMENT ON TABLE trigger_deliveries IS
    'Every inbound webhook delivery and what the endpoint decided. Written by the hook itself, '
    'so it survives the worker being down — which is when it matters most.';
