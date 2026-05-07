-- Initial schema for the minions Postgres backend.
--
-- Design: each row keeps its full Pydantic dump in a JSONB ``payload``
-- column, plus the small set of columns we actually filter or sort on.
-- This keeps the schema robust against Pydantic model drift; we only
-- need a new migration when a *queried* field changes.

CREATE TABLE IF NOT EXISTS decisions (
    id UUID PRIMARY KEY,
    project TEXT NOT NULL,
    status TEXT NOT NULL,
    type TEXT NOT NULL,
    risk TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    resolved_at TIMESTAMPTZ,
    payload JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS decisions_status_idx ON decisions(status);
CREATE INDEX IF NOT EXISTS decisions_project_idx ON decisions(project);
CREATE INDEX IF NOT EXISTS decisions_created_at_idx ON decisions(created_at DESC);

CREATE TABLE IF NOT EXISTS audit_findings (
    id TEXT PRIMARY KEY,
    source_project TEXT,
    source_pr_url TEXT,
    source_decision_id UUID,
    category TEXT NOT NULL,
    severity TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    payload JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_findings_status_idx ON audit_findings(status);
CREATE INDEX IF NOT EXISTS audit_findings_pr_url_idx ON audit_findings(source_pr_url);

CREATE TABLE IF NOT EXISTS engineer_runs (
    decision_id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    pr_url TEXT,
    pr_state TEXT,
    completed_at TIMESTAMPTZ NOT NULL,
    payload JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS engineer_runs_project_idx ON engineer_runs(project);
CREATE INDEX IF NOT EXISTS engineer_runs_pr_state_idx ON engineer_runs(pr_state);

-- Append-only ledgers.
CREATE TABLE IF NOT EXISTS cost_log (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    project TEXT,
    role TEXT,
    decision_id TEXT,
    model TEXT NOT NULL,
    in_tokens INTEGER NOT NULL DEFAULT 0,
    out_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd DOUBLE PRECISION NOT NULL DEFAULT 0,
    payload JSONB
);
CREATE INDEX IF NOT EXISTS cost_log_ts_idx ON cost_log(ts DESC);
CREATE INDEX IF NOT EXISTS cost_log_project_idx ON cost_log(project);

CREATE TABLE IF NOT EXISTS activity_log (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    event TEXT NOT NULL,
    project TEXT,
    role TEXT,
    decision_id TEXT,
    payload JSONB
);
CREATE INDEX IF NOT EXISTS activity_log_ts_idx ON activity_log(ts DESC);
