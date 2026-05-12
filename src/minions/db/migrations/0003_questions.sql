-- Inter-agent escalation channel. Mirrors the decisions table shape so the
-- two stay analogous: id PK + indexed status + JSONB payload for the rest.

CREATE TABLE IF NOT EXISTS questions (
    id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    status TEXT NOT NULL,
    target_role TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    payload JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS questions_status_idx ON questions(status);
CREATE INDEX IF NOT EXISTS questions_project_idx ON questions(project);
CREATE INDEX IF NOT EXISTS questions_target_role_idx ON questions(target_role);
