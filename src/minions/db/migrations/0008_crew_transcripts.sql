-- Crew transcripts: per-task LLM output captured for the Stage feed +
-- the per-run transcript page. See
-- ``openspec/changes/crew-transcripts/`` for the contract.
--
-- payload holds the full Pydantic CrewTranscriptMessage (including the
-- markdown content body); columns expose fields needed for lookups.

CREATE TABLE IF NOT EXISTS crew_transcripts (
    id UUID PRIMARY KEY,
    run_id TEXT NOT NULL,
    project TEXT NOT NULL,
    crew TEXT NOT NULL,
    agent_role TEXT NOT NULL,
    sequence INT NOT NULL,
    role_in_conversation TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    payload JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS crew_transcripts_run_idx ON crew_transcripts(run_id);
CREATE INDEX IF NOT EXISTS crew_transcripts_project_created_idx
    ON crew_transcripts(project, created_at DESC);
