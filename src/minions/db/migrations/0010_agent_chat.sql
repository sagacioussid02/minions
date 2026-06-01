-- Surface B (living-org-spaces) — operator click-to-chat with any agent.
-- One thread per (operator, agent) conversation; many messages per thread.
-- payload holds the full Pydantic model (forward-compat); columns expose the
-- fields needed for lookups + ordering.

CREATE TABLE IF NOT EXISTS agent_chat_threads (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    project TEXT,
    title TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    last_message_at TIMESTAMPTZ NOT NULL,
    payload JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS agent_chat_threads_agent_idx
    ON agent_chat_threads(agent_id, last_message_at DESC);

CREATE TABLE IF NOT EXISTS agent_chat_messages (
    id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL REFERENCES agent_chat_threads(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    model TEXT,
    prompt_tokens INT,
    response_tokens INT,
    payload JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS agent_chat_messages_thread_idx
    ON agent_chat_messages(thread_id, created_at ASC);
