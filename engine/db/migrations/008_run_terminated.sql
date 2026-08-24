-- 008 — a run stopped by a person is its own outcome.
--
-- Until now `flow_runs.status` had no value for "a human killed this". The nearest existing
-- one is `aborted`, which the flow writes when it stops *itself* — a verifier falsified the
-- change, or the retry budget ran out. Reusing it would make two different facts indis-
-- tinguishable in every report and every query: a change the system rejected on the evidence,
-- and a run somebody switched off. For a product whose output is the audit trail, collapsing
-- "we decided" into "you decided" is exactly the kind of loss that matters.
--
-- The distinction is also operational rather than academic. A terminated run has NOT
-- compensated: Temporal's terminate is abrupt by design, and the flow's own cleanup never
-- runs, so a branch is usually left on the remote. Anything that reconciles branches against
-- runs needs to be able to find these rows specifically.
--
-- The accompanying `run_terminated` event carries who did it, what the run was doing at the
-- time, and the branch that was left behind. This column is only the index into that.
ALTER TABLE flow_runs DROP CONSTRAINT flow_runs_status_check;

ALTER TABLE flow_runs ADD CONSTRAINT flow_runs_status_check
    CHECK (status = ANY (ARRAY[
        'running',
        'suspended',
        'succeeded',
        'rejected',
        'failed',
        'aborted',
        -- Stopped from outside the flow, by a named person. Never written by the workflow.
        'terminated'
    ]));

COMMENT ON COLUMN flow_runs.status IS
    'Lifecycle state. ''terminated'' is the only value written from outside the workflow, by '
    'the Workbench terminate endpoint; it implies compensation did NOT run and a branch may '
    'still exist on the remote. See the run''s ''run_terminated'' event for who and what.';
