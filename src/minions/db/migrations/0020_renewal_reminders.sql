-- Site Sentry "renewal radar": dated obligations to watch.
--
-- One row per declared license renewal / credential rotation, mirrored here
-- from each project manifest by the Site Sentry cron (replace-per-project on
-- every tick, so removing an item from a manifest clears its row). Dates
-- only — never the secret values. The operator console computes severity
-- (amber ≤30d, red ≤7d / overdue) against NOW() at read time.

CREATE TABLE IF NOT EXISTS renewal_reminders (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    project TEXT NOT NULL,
    kind TEXT NOT NULL,          -- 'license' | 'secret_rotation'
    name TEXT NOT NULL,
    due DATE NOT NULL,
    url TEXT,
    note TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, project, kind, name)
);

CREATE INDEX IF NOT EXISTS renewal_reminders_tenant_due_idx
    ON renewal_reminders(tenant_id, due);
