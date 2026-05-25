-- Durable compact memory for agents.
--
-- As with the other stores, ``payload`` keeps the full Pydantic record while
-- columns expose only the fields used for filtering, retrieval, and lifecycle.

CREATE TABLE IF NOT EXISTS agent_learning (
    id UUID PRIMARY KEY,
    agent_id TEXT NOT NULL,
    role TEXT NOT NULL,
    project TEXT,
    kind TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    confidence TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    last_used_at TIMESTAMPTZ,
    superseded_by UUID,
    expires_at TIMESTAMPTZ,
    payload JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS agent_learning_agent_idx ON agent_learning(agent_id);
CREATE INDEX IF NOT EXISTS agent_learning_role_project_kind_idx
    ON agent_learning(role, project, kind);
CREATE INDEX IF NOT EXISTS agent_learning_source_idx
    ON agent_learning(source_type, source_id);
CREATE INDEX IF NOT EXISTS agent_learning_created_at_idx
    ON agent_learning(created_at DESC);
