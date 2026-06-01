-- Site Sentry — continuous synthetic monitoring of every managed project's
-- declared `production_url` + `health_checks`. Append-only samples drive
-- p50/p99 + status; a tiny alert-state table gates notifier dedup.

CREATE TABLE IF NOT EXISTS site_health_samples (
    id BIGSERIAL PRIMARY KEY,
    project TEXT NOT NULL,
    check_path TEXT NOT NULL,
    ts TIMESTAMPTZ NOT NULL,
    ok BOOLEAN NOT NULL,
    status_code INTEGER,
    latency_ms INTEGER,
    error TEXT
);
CREATE INDEX IF NOT EXISTS site_health_samples_project_ts_idx
    ON site_health_samples(project, ts DESC);
CREATE INDEX IF NOT EXISTS site_health_samples_project_check_ts_idx
    ON site_health_samples(project, check_path, ts DESC);

-- Per (project, check_path) alert dedup + open-alert tracking. One row
-- per check; ``last_alert_kind`` is either ``down`` (an open alert) or
-- ``recovered`` (closed). Used to enforce the dedup_window and to send
-- exactly one "all clear" on recovery.
CREATE TABLE IF NOT EXISTS site_alert_state (
    project TEXT NOT NULL,
    check_path TEXT NOT NULL,
    last_alert_at TIMESTAMPTZ NOT NULL,
    last_alert_kind TEXT NOT NULL,
    PRIMARY KEY (project, check_path)
);
