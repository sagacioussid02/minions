-- Sprint numbering, task refinement, and per-agent memory.
-- See openspec/changes/sprint-tasks-memory/ for the full design.
--
-- Idempotent (CREATE/ALTER ... IF NOT EXISTS everywhere) so it is safe to
-- re-apply on dev databases that may have partial state from earlier runs.

-- ---------- Sprint numbering (per project) -------------------------------

CREATE TABLE IF NOT EXISTS sprint_counters (
    project TEXT PRIMARY KEY,
    current_sprint_number INT NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ---------- Structured plan + sprint stamp on existing decisions --------

ALTER TABLE decisions ADD COLUMN IF NOT EXISTS sprint_number INT;
ALTER TABLE decisions ADD COLUMN IF NOT EXISTS structured_plan JSONB;

CREATE INDEX IF NOT EXISTS decisions_project_sprint_idx
    ON decisions(project, sprint_number);

-- ---------- Tasks (post-refinement work chunks) --------------------------

CREATE TABLE IF NOT EXISTS tasks (
    id UUID PRIMARY KEY,
    decision_id UUID NOT NULL,
    project TEXT NOT NULL,
    sprint_number INT,
    category TEXT NOT NULL,   -- 'feature' | 'bug' | 'tech_debt' | 'ops' | 'docs'
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    acceptance_criteria TEXT,
    owner_role TEXT NOT NULL,
    owner_agent_id TEXT NOT NULL,
    owner_display_name TEXT,
    estimated_effort TEXT NOT NULL DEFAULT 'm',  -- xs|s|m|l|xl
    status TEXT NOT NULL DEFAULT 'queued',
                                                 -- queued|in_progress|review|done|blocked|cancelled
    pr_url TEXT,
    pr_number INT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    payload JSONB  -- catch-all for forward compat (acceptance notes, links, etc.)
);

CREATE INDEX IF NOT EXISTS tasks_decision_idx ON tasks(decision_id);
CREATE INDEX IF NOT EXISTS tasks_project_sprint_idx
    ON tasks(project, sprint_number, status);
CREATE INDEX IF NOT EXISTS tasks_owner_idx ON tasks(owner_agent_id, status);

-- ---------- Agent memory (hot/cold per named agent) ---------------------

CREATE TABLE IF NOT EXISTS agent_memory (
    id UUID PRIMARY KEY,
    agent_id TEXT NOT NULL,        -- "engineer@Demo"
    sprint_number INT,
    decision_id UUID,
    task_id UUID,
    pr_url TEXT,
    event TEXT NOT NULL,           -- task_started/task_done/pr_opened/...
    summary TEXT NOT NULL,         -- 1-2 sentence first-person note
    details TEXT,                  -- optional fuller context (cold-loaded)
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    tier TEXT NOT NULL DEFAULT 'hot'  -- 'hot' | 'cold'
);

CREATE INDEX IF NOT EXISTS agent_memory_agent_tier_idx
    ON agent_memory(agent_id, tier, created_at DESC);
CREATE INDEX IF NOT EXISTS agent_memory_sprint_idx
    ON agent_memory(agent_id, sprint_number);
