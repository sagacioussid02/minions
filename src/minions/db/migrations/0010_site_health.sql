-- Site Sentry: continuous synthetic health samples.
--
-- One row per probe per tick: the Site Sentry cron walks every active
-- manifest with a `deploy.production_url`, runs each `deploy.health_checks`
-- path (defaulting to `GET /`), and appends a sample here. The operator
-- console's Sentry page reads the latest sample per (project, check_path)
-- plus 24h p50/p99/uptime rollups straight off this table.
--
-- tenant_id is included from the start so the multi-tenant web layer can
-- scope reads without an out-of-band ALTER (unlike the pre-tenant tables).
-- Append-only; the table is expected to grow — prune with a retention job
-- if/when volume warrants it.

CREATE TABLE IF NOT EXISTS site_health_samples (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    project TEXT NOT NULL,
    check_path TEXT NOT NULL,
    ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ok BOOLEAN NOT NULL,
    status_code INTEGER,
    latency_ms INTEGER,
    error TEXT,
    -- TLS certificate expiry for the probed host, read off the HTTPS
    -- handshake (public data — no secret access). Same value stamped on
    -- every sample for the host; the read query surfaces the latest.
    cert_expires_at TIMESTAMPTZ
);

-- The read query does DISTINCT ON (project, check_path) ORDER BY ts DESC and
-- 24h window rollups, all scoped by tenant_id — this index serves both.
CREATE INDEX IF NOT EXISTS site_health_samples_tenant_project_check_ts_idx
    ON site_health_samples(tenant_id, project, check_path, ts DESC);
