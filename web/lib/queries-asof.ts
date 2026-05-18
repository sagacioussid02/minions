/**
 * "As-of" queries for /replay.
 *
 * Each function mirrors the live equivalent in queries.ts but filters
 * activity to `ts <= asOf`. We never reconstruct *true* past state (we
 * don't have full event sourcing) — we approximate by asking "given the
 * raw `activity_log` + `cost_log` up to this moment, what would the Floor
 * have shown?".
 *
 * Good enough for storytelling and demos; not a substitute for a real
 * historical store.
 */

import { sql } from "./db";
import {
  type ActivityEvent,
  type AgentState,
  type CostSummary,
  type HeroEvent,
} from "./schemas";
import { tierFor } from "./roles";
import { describe, deepLinks } from "./activity-renderer";

const MEANINGFUL_EVENTS = [
  "pr_opened",
  "pr_merged",
  "decision_resolved",
  "decision_submitted",
  "audit_finding_created",
  "question_submitted",
  "question_escalated",
  "engineer_run_completed",
];

export async function listActiveAgentsAt(asOf: Date): Promise<AgentState[]> {
  const s = sql();
  const rows = (await s`
    WITH all_events AS (
      SELECT project, role, ts, event, decision_id, error
      FROM activity_log
      WHERE ts <= ${asOf}::timestamptz
        AND ts > ${asOf}::timestamptz - INTERVAL '30 days'
        AND role IS NOT NULL
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
      WHERE al.ts <= ${asOf}::timestamptz
        AND al.ts > ${asOf}::timestamptz - INTERVAL '30 days'
        AND al.payload ? 'agents'
      UNION ALL
      SELECT project, role, ts, NULL::text AS event, decision_id, NULL::text AS error
      FROM cost_log
      WHERE ts <= ${asOf}::timestamptz
        AND ts > ${asOf}::timestamptz - INTERVAL '30 days'
        AND role IS NOT NULL
    ),
    recent AS (
      SELECT
        project,
        role,
        MAX(ts) AS last_event_at,
        (ARRAY_AGG(event ORDER BY ts DESC) FILTER (WHERE event IS NOT NULL))[1] AS last_event,
        (ARRAY_AGG(decision_id ORDER BY ts DESC) FILTER (WHERE decision_id IS NOT NULL))[1] AS last_decision_id,
        BOOL_OR(error IS NOT NULL AND ts > ${asOf}::timestamptz - INTERVAL '15 minutes') AS errored_recently
      FROM all_events
      GROUP BY project, role
    )
    SELECT
      r.project,
      r.role,
      r.last_event_at,
      r.last_event,
      r.last_decision_id,
      r.errored_recently
    FROM recent r
    ORDER BY r.project NULLS FIRST, r.role
  `) as Array<{
    project: string | null;
    role: string;
    last_event_at: Date | null;
    last_event: string | null;
    last_decision_id: string | null;
    errored_recently: boolean | null;
  }>;

  const asOfMs = asOf.getTime();
  return rows.map((r) => {
    const last = r.last_event_at ? r.last_event_at.getTime() : null;
    const inFlight = last !== null && asOfMs - last < 90_000;
    return {
      id: `${r.role}@${r.project ?? "shared"}`,
      display_name: null,
      role: r.role,
      role_tier: tierFor(r.role),
      project: r.project,
      tier: "unknown",
      seats: 1,
      last_event_at: r.last_event_at ? r.last_event_at.toISOString() : null,
      last_event: r.last_event,
      last_output: r.last_event,
      last_decision_id: r.last_decision_id,
      in_flight: inFlight,
      errored: Boolean(r.errored_recently),
      cost_today_usd: 0,
      recent_events: [],
      // Replay/as-of mode does not reconstruct the "currently running"
      // window — that signal is only meaningful for live state.
      live_run: null,
    };
  });
}

export async function listRecentEventsAt(
  asOf: Date,
  limit = 200,
): Promise<ActivityEvent[]> {
  const s = sql();
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
    WHERE ts <= ${asOf}::timestamptz
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

export async function getHeroEventAt(asOf: Date): Promise<HeroEvent> {
  const s = sql();
  let rows = (await s`
    SELECT ts, event, project, role, decision_id, crew, run_id, error, payload
    FROM activity_log
    WHERE event = ANY(${MEANINGFUL_EVENTS}) AND ts <= ${asOf}::timestamptz
    ORDER BY ts DESC
    LIMIT 1
  `) as Array<{
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

  if (rows.length === 0) {
    rows = (await s`
      SELECT ts, event, project, role, decision_id, crew, run_id, error, payload
      FROM activity_log
      WHERE event = 'crew_finished' AND ts <= ${asOf}::timestamptz
      ORDER BY ts DESC
      LIMIT 1
    `) as typeof rows;
  }
  if (rows.length === 0) return null;
  const r = rows[0];
  const event: ActivityEvent = {
    id: 0,
    ts: r.ts.toISOString(),
    event: r.event,
    project: r.project,
    role: r.role,
    decision_id: r.decision_id,
    crew: r.crew,
    run_id: r.run_id,
    error: r.error,
    payload: r.payload,
  };
  const links = deepLinks(event);
  const roleForSeed = r.role || r.crew || "system";
  return {
    ts: r.ts.toISOString(),
    event: r.event,
    project: r.project,
    role: r.role,
    role_tier: tierFor(roleForSeed),
    sentence: describe(event),
    deep_link_href: links[0]?.href ?? null,
    deep_link_label: links[0]?.label ?? null,
    avatar_seed: `${roleForSeed}@${r.project ?? "shared"}`,
  };
}

/** Earliest and latest timestamps we have data for — bounds for the slider. */
export async function activityTimeRange(): Promise<{
  earliest: string;
  latest: string;
}> {
  const s = sql();
  const rows = (await s`
    SELECT MIN(ts) AS earliest, MAX(ts) AS latest FROM activity_log
  `) as Array<{ earliest: Date | null; latest: Date | null }>;
  const r = rows[0] ?? { earliest: null, latest: null };
  const now = new Date();
  return {
    earliest: (r.earliest ?? new Date(now.getTime() - 7 * 24 * 3_600_000)).toISOString(),
    latest: (r.latest ?? now).toISOString(),
  };
}

export async function costSummaryAt(asOf: Date): Promise<CostSummary> {
  const s = sql();
  const WEEKLY_CAP_USD = Number(process.env.MINIONS_WEEKLY_CAP_USD ?? "50");
  const rows = (await s`
    SELECT
      COALESCE(SUM(CASE WHEN ts >= DATE_TRUNC('day', ${asOf}::timestamptz) THEN cost_usd ELSE 0 END), 0)::float8 AS today_usd,
      COALESCE(SUM(CASE WHEN ts >= DATE_TRUNC('week', ${asOf}::timestamptz) THEN cost_usd ELSE 0 END), 0)::float8 AS week_to_date_usd
    FROM cost_log
    WHERE ts <= ${asOf}::timestamptz
      AND ts >= DATE_TRUNC('week', ${asOf}::timestamptz)
  `) as Array<{ today_usd: number; week_to_date_usd: number }>;
  const row = rows[0] ?? { today_usd: 0, week_to_date_usd: 0 };
  return {
    today_usd: row.today_usd,
    week_to_date_usd: row.week_to_date_usd,
    week_cap_usd: WEEKLY_CAP_USD,
    fraction_of_week_cap: WEEKLY_CAP_USD ? row.week_to_date_usd / WEEKLY_CAP_USD : 0,
  };
}
