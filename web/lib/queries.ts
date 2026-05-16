/**
 * Typed read queries for the operator console.
 *
 * Every function here is callable from a Next.js route handler or directly
 * from an RSC. Each return type is validated by the matching schema in
 * `./schemas.ts`; the route handler is the final boundary that runs the
 * `.parse()` call.
 *
 * IMPORTANT — read-only by design. Operator writes (approve/reject/answer)
 * live in their own module so this file stays a pure read surface.
 */

import { sql } from "./db";
import {
  type ActivityEvent,
  type AgentState,
  type CostSummary,
  type HeadlineCounters,
  type Question,
  type WorkItem,
  type WorkItemStage,
} from "./schemas";
import { tierFor } from "./roles";

// ---------- Agents ----------

/**
 * Derive live agent state from activity_log + cost_log.
 *
 * v0 trick: we synthesize an `agents` list from *distinct (project, role)
 * combinations seen in activity_log over the past 30 days*. This matches the
 * Python roster without needing to mirror the YAML over here. Agents that
 * never produced an event simply do not appear yet.
 */
export async function listActiveAgents(): Promise<AgentState[]> {
  const s = sql();
  // The Python side writes role per-LLM-call into cost_log (most authoritative
  // for "who exists"), and per-event into activity_log.role when it has it. For
  // the many crew events where activity_log.role is null, derive role from
  // jsonb payload->'agents' (an array of role strings). Union the three sources
  // and pick the freshest event per (project, role).
  const rows = (await s`
    WITH all_events AS (
      SELECT project, role, ts, event, decision_id, error
      FROM activity_log
      WHERE ts > NOW() - INTERVAL '30 days' AND role IS NOT NULL
      UNION ALL
      SELECT
        al.project,
        agent_role AS role,
        al.ts,
        al.event,
        al.decision_id,
        al.error
      FROM activity_log al
      CROSS JOIN LATERAL jsonb_array_elements_text(
        COALESCE(al.payload->'agents', '[]'::jsonb)
      ) AS agent_role
      WHERE al.ts > NOW() - INTERVAL '30 days'
        AND al.payload ? 'agents'
      UNION ALL
      SELECT project, role, ts, NULL::text AS event, decision_id, NULL::text AS error
      FROM cost_log
      WHERE ts > NOW() - INTERVAL '30 days' AND role IS NOT NULL
    ),
    recent AS (
      SELECT
        project,
        role,
        MAX(ts) AS last_event_at,
        (ARRAY_AGG(event ORDER BY ts DESC) FILTER (WHERE event IS NOT NULL))[1] AS last_event,
        (ARRAY_AGG(decision_id ORDER BY ts DESC) FILTER (WHERE decision_id IS NOT NULL))[1] AS last_decision_id,
        BOOL_OR(error IS NOT NULL AND ts > NOW() - INTERVAL '15 minutes') AS errored_recently
      FROM all_events
      GROUP BY project, role
    ),
    cost_today AS (
      SELECT project, role, SUM(cost_usd)::float8 AS cost_today_usd
      FROM cost_log
      WHERE ts >= DATE_TRUNC('day', NOW())
      GROUP BY project, role
    )
    SELECT
      r.project,
      r.role,
      r.last_event_at,
      r.last_event,
      r.last_decision_id,
      r.errored_recently,
      COALESCE(c.cost_today_usd, 0)::float8 AS cost_today_usd,
      (r.last_event_at > NOW() - INTERVAL '90 seconds') AS in_flight
    FROM recent r
    LEFT JOIN cost_today c USING (project, role)
    ORDER BY r.project NULLS FIRST, r.role
  `) as Array<{
    project: string | null;
    role: string;
    last_event_at: Date | null;
    last_event: string | null;
    last_decision_id: string | null;
    errored_recently: boolean | null;
    cost_today_usd: number;
    in_flight: boolean | null;
  }>;

  return rows.map((r) => ({
    id: `${r.role}@${r.project ?? "shared"}`,
    display_name: null, // Python side owns the display-name registry; surface later.
    role: r.role,
    role_tier: tierFor(r.role),
    project: r.project,
    tier: "unknown", // Surface via roster sync in a follow-up.
    last_event_at: r.last_event_at ? r.last_event_at.toISOString() : null,
    last_event: r.last_event,
    last_decision_id: r.last_decision_id,
    in_flight: Boolean(r.in_flight),
    errored: Boolean(r.errored_recently),
    cost_today_usd: r.cost_today_usd,
  }));
}

// ---------- Work items (pipeline view) ----------

export async function listOpenWorkItems(): Promise<WorkItem[]> {
  const s = sql();
  // The Neon serverless template-tag only handles bound values; raw SQL
  // fragments are inlined directly in the query string. The stage CASE
  // stays inline.
  const rows = (await s`
    SELECT
      d.id::text AS decision_id,
      d.project,
      COALESCE(d.payload->>'summary', '(no summary)') AS summary,
      d.risk,
      CASE
        WHEN d.status = 'pending' THEN 'awaiting_you'
        WHEN d.status = 'rejected' THEN 'merged'
        WHEN d.status = 'timed_out' THEN 'merged'
        WHEN d.status = 'executed' AND er.pr_state = 'merged' THEN 'merged'
        WHEN d.status = 'executed' AND er.payload->>'ci_conclusion' = 'failure' THEN 'ci'
        WHEN d.status = 'executed' AND er.payload->>'ci_conclusion' = 'pending' THEN 'ci'
        WHEN d.status = 'executed' THEN 'review'
        WHEN d.status = 'approved' AND er.decision_id IS NULL THEN 'approved'
        WHEN d.status = 'approved' THEN 'coding'
        ELSE 'proposed'
      END AS stage,
      er.pr_url,
      (er.payload->>'pr_number')::int AS pr_number,
      er.payload->>'ci_conclusion' AS ci_conclusion,
      GREATEST(d.created_at, d.resolved_at, er.completed_at) AS stage_since
    FROM decisions d
    LEFT JOIN engineer_runs er ON er.decision_id = d.id::text
    WHERE d.status IN ('pending', 'approved', 'executed')
      AND COALESCE(er.pr_state, 'open') IN ('open', 'closed')
      AND COALESCE(d.payload->>'summary', '') NOT LIKE '%[DRY RUN]%'
    ORDER BY GREATEST(d.created_at, d.resolved_at, er.completed_at) DESC NULLS LAST
    LIMIT 50
  `) as Array<{
    decision_id: string;
    project: string;
    summary: string;
    risk: "low" | "medium" | "high";
    stage: WorkItemStage;
    pr_url: string | null;
    pr_number: number | null;
    ci_conclusion: string | null;
    stage_since: Date | null;
  }>;

  const now = Date.now();
  return rows
    .filter((r) => r.stage !== "merged") // keep view focused on in-flight
    .map((r) => {
      const stageSince = r.stage_since ? r.stage_since.getTime() : now;
      const ageHours = (now - stageSince) / 3_600_000;
      const stalled =
        (r.stage === "awaiting_you" && ageHours > 24) ||
        (r.stage === "ci" && r.ci_conclusion === "failure" && ageHours > 6) ||
        (r.stage === "coding" && ageHours > 0.5);

      return {
        decision_id: r.decision_id,
        project: r.project,
        summary: r.summary,
        risk: r.risk,
        stage: r.stage,
        pr_url: r.pr_url,
        pr_number: r.pr_number,
        ci_conclusion: r.ci_conclusion,
        stage_since: (r.stage_since ?? new Date()).toISOString(),
        stalled,
      };
    });
}

// ---------- Activity stream ----------

export async function listRecentEvents(opts: {
  limit?: number;
  sinceId?: number;
  project?: string;
  role?: string;
  event?: string;
}): Promise<ActivityEvent[]> {
  const s = sql();
  const limit = Math.min(opts.limit ?? 100, 500);
  const sinceId = opts.sinceId ?? 0;

  // Filter clauses are stitched conditionally so we don't send empty SQL.
  const rows = (await s`
    SELECT
      id::int8 AS id,
      ts,
      event,
      project,
      role,
      decision_id,
      crew,
      run_id,
      error,
      payload
    FROM activity_log
    WHERE id > ${sinceId}
      AND (${opts.project ?? null}::text IS NULL OR project = ${opts.project ?? null})
      AND (${opts.role ?? null}::text IS NULL OR role = ${opts.role ?? null})
      AND (${opts.event ?? null}::text IS NULL OR event = ${opts.event ?? null})
    ORDER BY id DESC
    LIMIT ${limit}
  `) as Array<{
    id: number | bigint;
    ts: Date;
    event: string;
    project: string | null;
    role: string | null;
    decision_id: string | null;
    crew: string | null;
    run_id: string | null;
    error: string | null;
    payload: Record<string, unknown> | null;
  }>;

  return rows.map((r) => ({
    id: Number(r.id),
    ts: r.ts.toISOString(),
    event: r.event,
    project: r.project,
    role: r.role,
    decision_id: r.decision_id,
    crew: r.crew,
    run_id: r.run_id,
    error: r.error,
    payload: r.payload,
  }));
}

// ---------- Cost ----------

const WEEKLY_CAP_USD = Number(process.env.MINIONS_WEEKLY_CAP_USD ?? "50");

export async function costSummary(): Promise<CostSummary> {
  const s = sql();
  const rows = (await s`
    SELECT
      COALESCE(SUM(CASE WHEN ts >= DATE_TRUNC('day', NOW()) THEN cost_usd ELSE 0 END), 0)::float8 AS today_usd,
      COALESCE(SUM(CASE WHEN ts >= DATE_TRUNC('week', NOW()) THEN cost_usd ELSE 0 END), 0)::float8 AS week_to_date_usd
    FROM cost_log
    WHERE ts >= DATE_TRUNC('week', NOW())
  `) as Array<{ today_usd: number; week_to_date_usd: number }>;

  const row = rows[0] ?? { today_usd: 0, week_to_date_usd: 0 };
  return {
    today_usd: row.today_usd,
    week_to_date_usd: row.week_to_date_usd,
    week_cap_usd: WEEKLY_CAP_USD,
    fraction_of_week_cap: WEEKLY_CAP_USD
      ? row.week_to_date_usd / WEEKLY_CAP_USD
      : 0,
  };
}

// ---------- Questions ----------

export async function listOpenQuestions(): Promise<Question[]> {
  const s = sql();
  const rows = (await s`
    SELECT
      id,
      project,
      payload->>'asker_role' AS asker_role,
      payload->>'asker_agent_id' AS asker_agent_id,
      target_role,
      payload->>'question' AS question,
      payload->>'context' AS context,
      payload->>'related_decision_id' AS related_decision_id,
      payload->>'related_pr_url' AS related_pr_url,
      status,
      created_at,
      (payload->>'escalated_at')::timestamptz AS escalated_at
    FROM questions
    WHERE status IN ('open', 'escalated')
    ORDER BY created_at DESC
    LIMIT 50
  `) as Array<{
    id: string;
    project: string;
    asker_role: string;
    asker_agent_id: string;
    target_role: string;
    question: string;
    context: string | null;
    related_decision_id: string | null;
    related_pr_url: string | null;
    status: "open" | "escalated";
    created_at: Date;
    escalated_at: Date | null;
  }>;

  return rows.map((r) => ({
    id: r.id,
    project: r.project,
    asker_role: r.asker_role,
    asker_agent_id: r.asker_agent_id,
    target_role: r.target_role,
    question: r.question,
    context: r.context,
    related_decision_id: r.related_decision_id,
    related_pr_url: r.related_pr_url,
    status: r.status,
    created_at: r.created_at.toISOString(),
    escalated_at: r.escalated_at ? r.escalated_at.toISOString() : null,
  }));
}

// ---------- Headline counters ----------

export async function headlineCounters(): Promise<HeadlineCounters> {
  const s = sql();
  const rows = (await s`
    SELECT
      (SELECT COUNT(*)::int FROM engineer_runs WHERE pr_state = 'open') AS open_prs,
      (SELECT COUNT(*)::int FROM decisions WHERE status = 'pending'
        AND COALESCE(payload->>'summary', '') NOT LIKE '%[DRY RUN]%'
      ) AS pending_approvals,
      (SELECT COUNT(DISTINCT project || ':' || role)::int FROM activity_log
        WHERE ts > NOW() - INTERVAL '5 minutes' AND role IS NOT NULL
      ) AS agents_active_5min,
      (SELECT COUNT(*)::int FROM decisions
        WHERE status = 'approved'
          AND payload->>'proposer_role' = 'pr_followup'
      ) AS queued_fixes
  `) as Array<HeadlineCounters>;
  return (
    rows[0] ?? {
      open_prs: 0,
      pending_approvals: 0,
      agents_active_5min: 0,
      queued_fixes: 0,
    }
  );
}
