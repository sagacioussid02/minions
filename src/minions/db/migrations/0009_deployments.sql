-- Post-merge deployment verification records.
--
-- One row per (project, sha) pair (find_by_sha is the canonical lookup).
-- payload holds the full Pydantic DeploymentRecord including the per-probe
-- HealthCheckResult list and findings markdown; columns expose only the
-- fields needed for indexed lookups.

CREATE TABLE IF NOT EXISTS deployments (
    id UUID PRIMARY KEY,
    project TEXT NOT NULL,
    merge_sha TEXT NOT NULL,
    status TEXT NOT NULL,
    deploy_target TEXT NOT NULL,
    pr_number INTEGER,
    detected_at TIMESTAMPTZ NOT NULL,
    verified_at TIMESTAMPTZ,
    payload JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS deployments_project_sha_idx
    ON deployments(project, merge_sha);
CREATE INDEX IF NOT EXISTS deployments_project_detected_idx
    ON deployments(project, detected_at DESC);
CREATE INDEX IF NOT EXISTS deployments_status_idx ON deployments(status);
