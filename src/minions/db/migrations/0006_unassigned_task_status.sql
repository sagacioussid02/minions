-- Make Task owner nullable so the refinement crew can produce
-- `unassigned` Tasks when every eligible agent is at WIP cap.
-- See openspec/changes/enriched-sprint-planning/ for the full design.

ALTER TABLE tasks ALTER COLUMN owner_agent_id DROP NOT NULL;
ALTER TABLE tasks ALTER COLUMN owner_display_name DROP NOT NULL;

-- Index helps the backlog sweep find unassigned tasks fast.
CREATE INDEX IF NOT EXISTS tasks_unassigned_idx
    ON tasks(project, created_at)
    WHERE status = 'unassigned';
