-- 009 — which files a run changed, as an index rather than as prose.
--
-- The fact already exists twice: git computed it inside the sandbox, and the Push node writes
-- it into its own notes as a "Files written" section. Neither form can answer the question
-- that matters, which is the *reverse* one:
--
--     which runs have changed components/Header.tsx?
--
-- Four runs for ticket 50 branched from one base, all edited that file, none could see the
-- others, and two of them were merged — the second by hand, into a state nothing had
-- verified. Every fact needed to see that collision coming was already recorded and none of
-- it was reachable without reading four runs' notes one at a time.
--
-- Per ADR 0012 this table creates no knowledge. It indexes what git already decided, so it is
-- never authoritative: if this and git ever disagree, git is right and this row is stale.
CREATE TABLE run_files (
    run_id   UUID   NOT NULL REFERENCES flow_runs(id),
    path     TEXT   NOT NULL,
    -- From `git diff --numstat`, not from the model's account of what it wrote. An agent's
    -- claim about its own change is never an input — invariant 3, applied to the index.
    added    INT    NOT NULL,
    removed  INT    NOT NULL,
    -- Which push this came from. A run can push more than once; the row carries the latest,
    -- because that is what actually reached the branch.
    attempt  INT    NOT NULL DEFAULT 0,

    PRIMARY KEY (run_id, path)
);

-- The reverse question is the whole reason for the table, so it gets the index.
CREATE INDEX run_files_path_idx ON run_files (path);

-- Not append-only, and deliberately so. `flow_events` is the record and carries every push
-- separately under invariant 9; this is a derived index over the latest state of a run, and
-- a later attempt is expected to overwrite an earlier one's line counts. Anything auditing
-- what happened reads the events, not this.
COMMENT ON TABLE run_files IS
    'Derived index: which files each run changed. Authoritative source is git, via flow_events.';
