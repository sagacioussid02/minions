/**
 * Cost accounting + budget cap for Surface B chat (X1).
 *
 * Mirrors the Python pricing table in ``minions/cost.py`` so a Haiku call
 * from either runtime is billed the same. Every chat reply writes one
 * cost_log row tagged ``payload.surface='agent_chat'`` — that's how
 * ``minions cost`` reads back the per-surface spend split.
 *
 * Budget cap: ``MINIONS_AGENT_CHAT_DAILY_BUDGET_USD`` (default $1) — the
 * route refuses new chat turns with HTTP 429 once today's cumulative
 * agent-chat spend crosses the cap.
 */

import { sql } from "../db";

// Anthropic public pricing as of 2026-Q2 (USD per 1M tokens). Unknown
// models cost 0 — we'd rather under-report than block.
const PRICING: Record<string, { input: number; output: number }> = {
  "haiku-4.5": { input: 1.0, output: 5.0 },
  "sonnet-4.6": { input: 3.0, output: 15.0 },
  "opus-4.7": { input: 15.0, output: 75.0 },
  haiku: { input: 1.0, output: 5.0 },
  sonnet: { input: 3.0, output: 15.0 },
  opus: { input: 15.0, output: 75.0 },
};

export const DEFAULT_DAILY_BUDGET_USD = 1.0;

export function resolveTier(model: string): string | null {
  const m = model.toLowerCase().replace(/_/g, "-");
  if (m.includes("haiku")) return "haiku";
  if (m.includes("sonnet")) return "sonnet";
  if (m.includes("opus")) return "opus";
  return null;
}

export function estimateCostUsd(
  model: string,
  inputTokens: number,
  outputTokens: number,
): number {
  const tier = resolveTier(model);
  if (!tier) return 0;
  const p = PRICING[tier];
  return (inputTokens * p.input + outputTokens * p.output) / 1_000_000;
}

export function dailyBudgetCap(): number {
  const raw = process.env.MINIONS_AGENT_CHAT_DAILY_BUDGET_USD;
  if (!raw) return DEFAULT_DAILY_BUDGET_USD;
  const parsed = Number.parseFloat(raw);
  if (Number.isNaN(parsed) || parsed < 0) return DEFAULT_DAILY_BUDGET_USD;
  return parsed;
}

/**
 * Return today's cumulative agent_chat spend in USD. Safe: returns 0 if
 * cost_log is empty/missing or anything goes wrong — never blocks a
 * legitimate operator turn behind an observability hiccup.
 */
export async function agentChatSpendTodayUsd(): Promise<number> {
  try {
    const s = sql();
    const rows = (await s`
      SELECT COALESCE(SUM(cost_usd), 0)::float8 AS total
      FROM cost_log
      WHERE ts >= DATE_TRUNC('day', NOW())
        AND payload->>'surface' = 'agent_chat'
    `) as Array<{ total: number }>;
    return rows[0]?.total ?? 0;
  } catch (err) {
    console.warn("[agent-chat cost] spend-today query failed; returning 0", err);
    return 0;
  }
}

/**
 * Record one chat-reply cost into cost_log. Best-effort — any failure is
 * logged but never bubbles up; observability must not break the chat.
 */
export async function recordChatCost(args: {
  agentId: string;
  threadId: string;
  project: string | null;
  role: string;
  model: string;
  inputTokens: number;
  outputTokens: number;
}): Promise<void> {
  const cost = estimateCostUsd(args.model, args.inputTokens, args.outputTokens);
  const payload = {
    surface: "agent_chat",
    agent_id: args.agentId,
    thread_id: args.threadId,
    project: args.project,
    role: args.role,
    model: args.model,
    input_tokens: args.inputTokens,
    output_tokens: args.outputTokens,
    cost_usd: Number(cost.toFixed(6)),
  };
  try {
    const s = sql();
    await s`
      INSERT INTO cost_log (
        ts, project, role, decision_id, model,
        in_tokens, out_tokens, cost_usd, payload
      ) VALUES (
        NOW(), ${args.project}, ${args.role}, NULL, ${args.model},
        ${args.inputTokens}, ${args.outputTokens}, ${cost},
        ${JSON.stringify(payload)}::jsonb
      )
    `;
  } catch (err) {
    console.warn("[agent-chat cost] cost_log insert failed", err);
  }
}
