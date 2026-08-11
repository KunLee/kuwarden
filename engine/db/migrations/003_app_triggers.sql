-- 003 — which tickets an application accepts.
--
-- Admission control lives here: a ticket that does not match is refused at intake rather
-- than discovered three nodes later, once a model has already read it.
--
-- This duplicates the `triggers` section of the application's own `kuwarden.yaml`. The
-- duplication is deliberate and temporary: the Workbench needs somewhere writable at runtime
-- to register an application before that file exists. Generating `kuwarden.yaml` from these
-- rows -- so the application's repository owns its own configuration, reviewed like code --
-- is owed, and is the reason this table is shaped like that file rather than like a form.

CREATE TABLE app_triggers (
    id            UUID PRIMARY KEY,
    app_id        UUID NOT NULL REFERENCES app_registry(id) ON DELETE CASCADE,

    provider      TEXT NOT NULL CHECK (provider IN ('jira', 'azure_devops')),

    -- Jira: the site URL and the account the token belongs to.
    -- Azure DevOps: the organisation. Exactly one set applies, enforced below.
    site          TEXT,
    account_email TEXT,
    organisation  TEXT,

    project       TEXT NOT NULL,

    -- Admission control. NULL means "no filter", which is a decision an operator makes
    -- rather than a default we apply: an application that accepts every ticket in a project
    -- should have said so.
    label             TEXT,
    max_story_points  INT,
    -- Jira stores story points in a custom field whose id differs per instance. There is no
    -- sane default; assuming one silently reads the wrong field, or nothing.
    story_points_field TEXT,

    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT jira_needs_a_site CHECK (
        provider <> 'jira' OR (site IS NOT NULL AND account_email IS NOT NULL)
    ),
    CONSTRAINT azure_devops_needs_an_organisation CHECK (
        provider <> 'azure_devops' OR organisation IS NOT NULL
    ),
    -- One trigger per provider+project per application. A second one for the same project
    -- would make "which rule admitted this ticket" ambiguous, and that question has to have
    -- one answer in an audit record.
    UNIQUE (app_id, provider, project)
);

CREATE INDEX app_triggers_app_idx ON app_triggers (app_id);
