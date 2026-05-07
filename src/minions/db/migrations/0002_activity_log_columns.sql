-- 0001 left activity_log with a single ``role`` column, but the model
-- stores ``crew`` (string) plus ``agents`` (list of role names) and
-- ``run_id`` correlating start↔finish events. Round out the columns
-- without dropping ``role`` (still valid as a denorm if we ever want it).

ALTER TABLE activity_log ADD COLUMN IF NOT EXISTS crew TEXT;
ALTER TABLE activity_log ADD COLUMN IF NOT EXISTS run_id TEXT;
ALTER TABLE activity_log ADD COLUMN IF NOT EXISTS error TEXT;

CREATE INDEX IF NOT EXISTS activity_log_run_id_idx ON activity_log(run_id);
CREATE INDEX IF NOT EXISTS activity_log_project_event_idx
    ON activity_log(project, event);
