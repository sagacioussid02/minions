/**
 * Cost accounting + budget cap for Surface B chat (X1).
 *
 * Mirrors the Python pricing table in ``minions/cost.py`` so a Haiku call
 * from either runtime is billed the same. Every chat reply writes one
 * cost_log row tagged ``payload.surface='agent_chat'`` — that's how
 * ``minions cost`` reads back the per-surface spend split.
 *
 * Limits (all enforced by the chat route with HTTP 429; reset at midnight UTC):
 * - Per-tenant daily cap: the tenant's own ``cost_cap_daily_usd`` (free tier
 *   $1), never above ``MINIONS_AGENT_CHAT_DAILY_BUDGET_USD`` (default $1).
 *   The founder uses the env cap alone.
 * - Global hard cap across ALL tenants:
 *   ``MINIONS_AGENT_CHAT_GLOBAL_DAILY_BUDGET_USD`` (default $10), so many
 *   free sign-ups can't add up to an unbounded bill on the shared key.
 * - Per-tenant rate limit: ``MINIONS_AGENT_CHAT_RATE_PER_MIN`` user turns per
 *   rolling minute (default 6).
 */

import { sql } from "../db";
import { getTenantId } from "../tenant";

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
export const DEFAULT_GLOBAL_DAILY_BUDGET_USD = 10.0;
export const DEFAULT_RATE_PER_MIN = 6;

function envNumber(name: string, fallback: number): number {
  const raw = process.env[name];
  if (!raw) return fallback;
  const parsed = Number.parseFloat(raw);
  if (Number.isNaN(parsed) || parsed < 0) return fallback;
  return parsed;
}

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
  return envNumber("MINIONS_AGENT_CHAT_DAILY_BUDGET_USD", DEFAULT_DAILY_BUDGET_USD);
}

export function globalDailyBudgetCap(): number {
  return envNumber("MINIONS_AGENT_CHAT_GLOBAL_DAILY_BUDGET_USD", DEFAULT_GLOBAL_DAILY_BUDGET_USD);
}

export function chatRatePerMinute(): number {
  return Math.floor(envNumber("MINIONS_AGENT_CHAT_RATE_PER_MIN", DEFAULT_RATE_PER_MIN));
}

/** The daily chat cap for one tenant: its own cap, never above the env cap. */
export function tenantDailyCap(tenant: {
  founder: boolean;
  cost_cap_daily_usd: number | string;
}): number {
  const envCap = dailyBudgetCap();
  if (tenant.founder) return envCap;
  const own = Number(tenant.cost_cap_daily_usd);
  return Number.isFinite(own) && own >= 0 ? Math.min(own, envCap) : envCap;
}

/**
 * Return the signed-in tenant's cumulative agent_chat spend today, in USD.
 * Per tenant, so one tenant can't use up another's daily cap. Safe: returns 0 if
 * cost_log is empty/missing or anything goes wrong — never blocks a
 * legitimate operator turn behind an observability hiccup.
 */
export async function agentChatSpendTodayUsd(): Promise<number> {
  try {
    const s = sql();
    const tid = await getTenantId();
    const rows = (await s`
      SELECT COALESCE(SUM(cost_usd), 0)::float8 AS total
      FROM cost_log
      WHERE ts >= DATE_TRUNC('day', NOW())
        AND tenant_id = ${tid}
        AND payload->>'surface' = 'agent_chat'
    `) as Array<{ total: number }>;
    return rows[0]?.total ?? 0;
  } catch (err) {
    console.warn("[agent-chat cost] spend-today query failed; returning 0", err);
    return 0;
  }
}

/**
 * Today's agent_chat spend across ALL tenants, for the global hard cap.
 * Fails closed (returns Infinity) so a broken query can't lift the cap.
 */
export async function agentChatSpendTodayAllTenantsUsd(): Promise<number> {
  try {
    const s = sql();
    // tenant-scoping: allow-global — the global cap must sum every tenant.
    const rows = (await s`
      SELECT COALESCE(SUM(cost_usd), 0)::float8 AS total
      FROM cost_log
      WHERE ts >= DATE_TRUNC('day', NOW())
        AND payload->>'surface' = 'agent_chat'
    `) as Array<{ total: number }>;
    return rows[0]?.total ?? 0;
  } catch (err) {
    console.warn("[agent-chat cost] global spend query failed; failing closed", err);
    return Number.POSITIVE_INFINITY;
  }
}

/** User chat turns the signed-in tenant sent in the last 60 seconds. */
export async function userTurnsLastMinute(): Promise<number> {
  try {
    const s = sql();
    const tid = await getTenantId();
    const rows = (await s`
      SELECT COUNT(*)::int AS n
      FROM agent_chat_messages
      WHERE tenant_id = ${tid}
        AND role = 'user'
        AND created_at > NOW() - INTERVAL '60 seconds'
    `) as Array<{ n: number }>;
    return rows[0]?.n ?? 0;
  } catch (err) {
    console.warn("[agent-chat cost] rate query failed; returning 0", err);
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
    const tid = await getTenantId();
    await s`
      INSERT INTO cost_log (
        ts, project, role, decision_id, model,
        in_tokens, out_tokens, cost_usd, payload, tenant_id
      ) VALUES (
        NOW(), ${args.project}, ${args.role}, NULL, ${args.model},
        ${args.inputTokens}, ${args.outputTokens}, ${cost},
        ${JSON.stringify(payload)}::jsonb, ${tid}
      )
    `;
  } catch (err) {
    console.warn("[agent-chat cost] cost_log insert failed", err);
  }
}
