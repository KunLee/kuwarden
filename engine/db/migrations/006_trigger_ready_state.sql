-- 006 — the workflow state that admits a ticket.
--
-- The trigger that scales. A ticket *save* fires on every field change — a reassignment, a
-- typo fix, a tag — and admitting a run on each of those spends someone's model budget on
-- activity rather than on intent. Moving a ticket into a named state is a deliberate act, so
-- admission reads an intention instead of inferring one.
--
-- It also matters before any webhook exists: the manual "Start run" path checks it too, so a
-- ticket nobody marked ready is refused at Triage rather than half-implemented by an agent.
--
-- NULL means the state is not checked, which stays the default. Same posture as `label`: an
-- application that admits a ticket in any state should have said so, not inherited it.

ALTER TABLE app_triggers ADD COLUMN ready_state TEXT;

COMMENT ON COLUMN app_triggers.ready_state IS
    'Workflow state a ticket must be in to be admitted, e.g. "Ready for Agent". '
    'NULL means state is not checked. Compared case-insensitively.';
