-- Per-project dossier drafts (PROJECT_DOSSIER.md state machine).
--
-- ``payload`` holds the full Pydantic ``DossierDraft`` (including the produced
-- markdown body and verifier log); columns expose only the fields needed for
-- lookups and freshness checks.

CREATE TABLE IF NOT EXISTS dossier_drafts (
    id UUID PRIMARY KEY,
    project TEXT NOT NULL,
    commit_sha TEXT NOT NULL,
    status TEXT NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL,
    pr_url TEXT,
    pr_number INTEGER,
    merged_at TIMESTAMPTZ,
    payload JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS dossier_drafts_project_idx ON dossier_drafts(project);
CREATE INDEX IF NOT EXISTS dossier_drafts_project_status_idx
    ON dossier_drafts(project, status);
CREATE INDEX IF NOT EXISTS dossier_drafts_generated_at_idx
    ON dossier_drafts(generated_at DESC);
