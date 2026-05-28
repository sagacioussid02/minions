/**
 * Postgres reads + writes for Surface B chat (B3).
 *
 * Shared with the Python side via the same Neon database. Tables created in
 * minions/db/migrations/0010_agent_chat.sql; learning + transcripts + dossier
 * tables are migrations 0004 / 0008 / 0007.
 */

import { randomUUID } from "node:crypto";
import { sql } from "../db";

// ---------- Threads + messages (R/W) ----------

export type ThreadRow = {
  id: string;
  agent_id: string;
  project: string | null;
  title: string | null;
  created_at: string;
  last_message_at: string;
};

export type MessageRow = {
  id: string;
  thread_id: string;
  role: "user" | "agent";
  content: string;
  created_at: string;
  model: string | null;
  prompt_tokens: number | null;
  response_tokens: number | null;
};

async function ensureChatTable(): Promise<boolean> {
  const s = sql();
  const rows = (await s`
    SELECT to_regclass('public.agent_chat_threads') IS NOT NULL AS ok
  `) as Array<{ ok: boolean }>;
  return Boolean(rows[0]?.ok);
}

export async function getThread(threadId: string): Promise<ThreadRow | null> {
  if (!(await ensureChatTable())) return null;
  const s = sql();
  const rows = (await s`
    SELECT id, agent_id, project, title, created_at, last_message_at
    FROM agent_chat_threads
    WHERE id = ${threadId}
    LIMIT 1
  `) as Array<{
    id: string;
    agent_id: string;
    project: string | null;
    title: string | null;
    created_at: Date;
    last_message_at: Date;
  }>;
  const r = rows[0];
  if (!r) return null;
  return {
    ...r,
    created_at: r.created_at.toISOString(),
    last_message_at: r.last_message_at.toISOString(),
  };
}

export async function listThreadsForAgent(agentId: string): Promise<ThreadRow[]> {
  if (!(await ensureChatTable())) return [];
  const s = sql();
  const rows = (await s`
    SELECT id, agent_id, project, title, created_at, last_message_at
    FROM agent_chat_threads
    WHERE agent_id = ${agentId}
    ORDER BY last_message_at DESC
    LIMIT 100
  `) as Array<{
    id: string;
    agent_id: string;
    project: string | null;
    title: string | null;
    created_at: Date;
    last_message_at: Date;
  }>;
  return rows.map((r) => ({
    ...r,
    created_at: r.created_at.toISOString(),
    last_message_at: r.last_message_at.toISOString(),
  }));
}

export async function listMessages(threadId: string): Promise<MessageRow[]> {
  if (!(await ensureChatTable())) return [];
  const s = sql();
  const rows = (await s`
    SELECT id, thread_id, role, content, created_at, model, prompt_tokens, response_tokens
    FROM agent_chat_messages
    WHERE thread_id = ${threadId}
    ORDER BY created_at ASC
  `) as Array<{
    id: string;
    thread_id: string;
    role: "user" | "agent";
    content: string;
    created_at: Date;
    model: string | null;
    prompt_tokens: number | null;
    response_tokens: number | null;
  }>;
  return rows.map((r) => ({
    ...r,
    created_at: r.created_at.toISOString(),
  }));
}

export async function createThread(args: {
  agentId: string;
  project: string | null;
  title: string | null;
}): Promise<ThreadRow> {
  const s = sql();
  const id = randomUUID();
  const now = new Date();
  const payload = {
    id,
    agent_id: args.agentId,
    project: args.project,
    title: args.title,
    created_at: now.toISOString(),
    last_message_at: now.toISOString(),
  };
  await s`
    INSERT INTO agent_chat_threads (
      id, agent_id, project, title, created_at, last_message_at, payload
    ) VALUES (
      ${id}, ${args.agentId}, ${args.project}, ${args.title}, ${now}, ${now}, ${JSON.stringify(payload)}::jsonb
    )
  `;
  return {
    id,
    agent_id: args.agentId,
    project: args.project,
    title: args.title,
    created_at: now.toISOString(),
    last_message_at: now.toISOString(),
  };
}

export async function appendMessage(args: {
  threadId: string;
  role: "user" | "agent";
  content: string;
  model?: string | null;
  promptTokens?: number | null;
  responseTokens?: number | null;
}): Promise<MessageRow> {
  const s = sql();
  const id = randomUUID();
  const now = new Date();
  const payload = {
    id,
    thread_id: args.threadId,
    role: args.role,
    content: args.content,
    created_at: now.toISOString(),
    model: args.model ?? null,
    prompt_tokens: args.promptTokens ?? null,
    response_tokens: args.responseTokens ?? null,
  };
  await s`
    INSERT INTO agent_chat_messages (
      id, thread_id, role, content, created_at,
      model, prompt_tokens, response_tokens, payload
    ) VALUES (
      ${id}, ${args.threadId}, ${args.role}, ${args.content}, ${now},
      ${args.model ?? null}, ${args.promptTokens ?? null}, ${args.responseTokens ?? null},
      ${JSON.stringify(payload)}::jsonb
    )
  `;
  await s`
    UPDATE agent_chat_threads SET last_message_at = ${now} WHERE id = ${args.threadId}
  `;
  return {
    id,
    thread_id: args.threadId,
    role: args.role,
    content: args.content,
    created_at: now.toISOString(),
    model: args.model ?? null,
    prompt_tokens: args.promptTokens ?? null,
    response_tokens: args.responseTokens ?? null,
  };
}

// ---------- Context inputs (read-only) ----------

export type LearningSnippet = {
  fact: string;
  kind: string;
  confidence: "high" | "medium" | "low";
};

export async function listLearningForAgent(
  agentId: string,
  limit = 15,
): Promise<LearningSnippet[]> {
  const s = sql();
  const hasTable = (await s`
    SELECT to_regclass('public.agent_learning') IS NOT NULL AS ok
  `) as Array<{ ok: boolean }>;
  if (!hasTable[0]?.ok) return [];
  // Active records only — exclude superseded + expired. Confidence first, recency second.
  const rows = (await s`
    SELECT payload
    FROM agent_learning
    WHERE agent_id = ${agentId}
      AND superseded_by IS NULL
      AND (expires_at IS NULL OR expires_at > NOW())
    ORDER BY
      CASE confidence WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END ASC,
      COALESCE(last_used_at, created_at) DESC
    LIMIT ${Math.min(Math.max(limit, 1), 50)}
  `) as Array<{ payload: Record<string, unknown> }>;
  return rows.map((r) => ({
    fact: String(r.payload.fact ?? ""),
    kind: String(r.payload.kind ?? "other"),
    confidence: (r.payload.confidence as LearningSnippet["confidence"]) ?? "low",
  }));
}

export type TranscriptSnippet = {
  crew: string;
  agent_role: string;
  content: string;
};

export async function listRecentTranscripts(
  project: string | null,
  limit = 5,
): Promise<TranscriptSnippet[]> {
  const s = sql();
  const hasTable = (await s`
    SELECT to_regclass('public.crew_transcripts') IS NOT NULL AS ok
  `) as Array<{ ok: boolean }>;
  if (!hasTable[0]?.ok) return [];
  const capped = Math.min(Math.max(limit, 1), 25);
  const rows = (project
    ? ((await s`
        SELECT crew, agent_role, payload->>'content' AS content
        FROM crew_transcripts
        WHERE project = ${project}
        ORDER BY created_at DESC
        LIMIT ${capped}
      `) as Array<{ crew: string; agent_role: string; content: string | null }>)
    : ((await s`
        SELECT crew, agent_role, payload->>'content' AS content
        FROM crew_transcripts
        ORDER BY created_at DESC
        LIMIT ${capped}
      `) as Array<{ crew: string; agent_role: string; content: string | null }>));
  return rows.map((r) => ({
    crew: r.crew,
    agent_role: r.agent_role,
    content: r.content ?? "",
  }));
}

export async function latestMergedDossierMarkdown(project: string): Promise<string | null> {
  const s = sql();
  const hasTable = (await s`
    SELECT to_regclass('public.dossier_drafts') IS NOT NULL AS ok
  `) as Array<{ ok: boolean }>;
  if (!hasTable[0]?.ok) return null;
  const rows = (await s`
    SELECT payload->>'markdown' AS markdown
    FROM dossier_drafts
    WHERE project = ${project} AND status = 'merged'
    ORDER BY merged_at DESC NULLS LAST, generated_at DESC
    LIMIT 1
  `) as Array<{ markdown: string | null }>;
  return rows[0]?.markdown ?? null;
}

export async function lookupDisplayName(args: {
  project: string | null;
  role: string;
}): Promise<string | null> {
  const s = sql();
  const rows = (await s`
    SELECT payload->>'proposer_display_name' AS display_name
    FROM decisions
    WHERE payload->>'proposer_role' = ${args.role}
      AND (
        ${args.project}::text IS NULL OR project = ${args.project}
      )
      AND payload ? 'proposer_display_name'
      AND payload->>'proposer_display_name' <> ''
    ORDER BY created_at DESC
    LIMIT 1
  `) as Array<{ display_name: string | null }>;
  return rows[0]?.display_name ?? null;
}
