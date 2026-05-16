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
  type HeroEvent,
  type Question,
  type WorkItem,
  type WorkItemStage,
} from "./schemas";
import { tierFor } from "./roles";
import { describe, deepLinks } from "./activity-renderer";
import {
  AUDIT,
  PER_PROJECT_ROLES,
  SHARED_EXECUTIVE,
  SHARED_SPECIALIST,
} from "./roster";

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
    ),
    -- Latest decision per (project, proposer_role) gives us a stable display
    -- name. The Python side fills proposer_display_name whenever it knows.
    display_names AS (
      SELECT DISTINCT ON (project, payload->>'proposer_role')
        project,
        payload->>'proposer_role' AS role,
        payload->>'proposer_display_name' AS display_name
      FROM decisions
      WHERE payload ? 'proposer_display_name'
        AND payload->>'proposer_display_name' <> ''
      ORDER BY project, payload->>'proposer_role', created_at DESC
    )
    SELECT
      r.project,
      r.role,
      r.last_event_at,
      r.last_event,
      r.last_decision_id,
      r.errored_recently,
      COALESCE(c.cost_today_usd, 0)::float8 AS cost_today_usd,
      (r.last_event_at > NOW() - INTERVAL '90 seconds') AS in_flight,
      dn.display_name
    FROM recent r
    LEFT JOIN cost_today c USING (project, role)
    LEFT JOIN display_names dn USING (project, role)
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
    display_name: string | null;
  }>;

  // ---- Scaffold the full configured roster ----------------------------------
  // The Floor wants to show every agent that *should exist* in the org, not
  // just those that have already fired. Pull the active project list from
  // Postgres + add SHARED_* roles, then union with the activity-derived data.

  // Distinct projects from activity_log + cost_log + decisions. Anything we
  // have a manifest for would show up in at least one of these once cron has
  // run a single dry-run; until then a freshly-cloned repo just shows
  // SHARED_*.
  const projectRows = (await s`
    SELECT DISTINCT project FROM (
      SELECT project FROM decisions
      UNION ALL
      SELECT project FROM activity_log
      UNION ALL
      SELECT project FROM cost_log
    ) all_projects
    WHERE project IS NOT NULL
      AND project NOT IN ('p', '')
  `) as Array<{ project: string | null }>;
  const projects = projectRows
    .map((r) => r.project)
    .filter((p): p is string => Boolean(p))
    .sort();

  // Build the (project, role, seats) cartesian for the configured roster.
  const configured: Array<{
    project: string | null;
    role: string;
    seats: number;
  }> = [];
  for (const project of projects) {
    for (const { role, seats } of PER_PROJECT_ROLES) {
      configured.push({ project, role, seats });
    }
  }
  for (const role of [...SHARED_EXECUTIVE, ...SHARED_SPECIALIST, ...AUDIT]) {
    configured.push({ project: null, role, seats: 1 });
  }

  // Lookup map for activity data keyed by `${role}@${project ?? "shared"}`.
  const activityByKey = new Map<string, (typeof rows)[number]>();
  for (const r of rows) {
    activityByKey.set(`${r.role}@${r.project ?? "shared"}`, r);
  }

  const merged = configured.map(({ project, role, seats }) => {
    const key = `${role}@${project ?? "shared"}`;
    const r = activityByKey.get(key);
    return {
      id: key,
      display_name: r?.display_name ?? null,
      role,
      role_tier: tierFor(role),
      project,
      tier: "unknown",
      seats,
      last_event_at: r?.last_event_at ? r.last_event_at.toISOString() : null,
      last_event: r?.last_event ?? null,
      last_decision_id: r?.last_decision_id ?? null,
      in_flight: Boolean(r?.in_flight),
      errored: Boolean(r?.errored_recently),
      cost_today_usd: r?.cost_today_usd ?? 0,
    };
  });

  // Surface any activity-derived agents that aren't in the static roster
  // (e.g. the `pr_followup` synthetic agent the PR-followup sweep emits).
  // Their seats default to 1.
  for (const r of rows) {
    const key = `${r.role}@${r.project ?? "shared"}`;
    if (configured.some((c) => `${c.role}@${c.project ?? "shared"}` === key)) {
      continue;
    }
    merged.push({
      id: key,
      display_name: r.display_name,
      role: r.role,
      role_tier: tierFor(r.role),
      project: r.project,
      tier: "unknown",
      seats: 1,
      last_event_at: r.last_event_at ? r.last_event_at.toISOString() : null,
      last_event: r.last_event,
      last_decision_id: r.last_decision_id,
      in_flight: Boolean(r.in_flight),
      errored: Boolean(r.errored_recently),
      cost_today_usd: r.cost_today_usd,
    });
  }

  return merged;
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

// ---------- Hero event ----------

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

/** Most attention-worthy recent event for the page hero strip. */
export async function getHeroEvent(): Promise<HeroEvent> {
  const s = sql();
  let rows = (await s`
    SELECT ts, event, project, role, decision_id, crew, run_id, error, payload
    FROM activity_log
    WHERE event = ANY(${MEANINGFUL_EVENTS})
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
    // Soft fallback: surface the freshest crew run.
    rows = (await s`
      SELECT ts, event, project, role, decision_id, crew, run_id, error, payload
      FROM activity_log
      WHERE event = 'crew_finished'
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

// ---------- Sprint Board ----------

import { type SprintBoard, type SprintCard, type SprintColumn } from "./schemas";

/**
 * Build the kanban view of the org.
 *
 * Lanes follow the operator narrative: idea → human gate → engineer → CI →
 * review → merge. Every visible card is a Decision Record in some state; the
 * Decision id is the stable key callers use for approve/reject/merge.
 *
 * Cards labelled `[DRY RUN]` are filtered out — they have no real plan and
 * pollute the board.
 */
export async function listSprintBoard(project?: string): Promise<SprintBoard> {
  const s = sql();

  // Projects strip for the tab UI — projects with at least one non-dry-run
  // decision, regardless of status, sorted alphabetically.
  const projectRows = (await s`
    SELECT DISTINCT project
    FROM decisions
    WHERE COALESCE(payload->>'summary', '') NOT LIKE '%[DRY RUN]%'
    ORDER BY project
  `) as Array<{ project: string }>;
  const projects = projectRows.map((r) => r.project);

  const rows = (await s`
    SELECT
      d.id::text AS decision_id,
      d.project,
      d.type,
      d.risk,
      d.created_at,
      d.resolved_at,
      COALESCE(d.payload->>'summary', '(no summary)') AS summary,
      d.payload->>'proposer_role' AS proposer_role,
      d.payload->>'proposer_display_name' AS proposer_display_name,
      (d.payload ? 'security_review') AS has_security_review,
      (d.payload ? 'critique') AS has_devils_advocate,
      CASE
        WHEN d.status = 'pending' THEN 'awaiting_you'
        WHEN d.status IN ('rejected', 'timed_out') THEN NULL
        WHEN d.status = 'executed' AND er.pr_state = 'merged' THEN 'done'
        WHEN d.status = 'executed' AND er.payload->>'ci_conclusion' = 'failure' THEN 'in_progress'
        WHEN d.status = 'executed' AND er.payload->>'ci_conclusion' = 'pending' THEN 'in_progress'
        WHEN d.status = 'executed' AND er.payload->>'ci_conclusion' = 'success' THEN 'review'
        WHEN d.status = 'executed' THEN 'review'
        WHEN d.status = 'approved' AND er.decision_id IS NULL THEN 'approved'
        WHEN d.status = 'approved' THEN 'in_progress'
        ELSE 'backlog'
      END AS column,
      er.pr_url,
      (er.payload->>'pr_number')::int AS pr_number,
      er.payload->>'ci_conclusion' AS ci_conclusion,
      GREATEST(d.created_at, d.resolved_at, er.completed_at) AS stage_since
    FROM decisions d
    LEFT JOIN engineer_runs er ON er.decision_id = d.id::text
    WHERE COALESCE(d.payload->>'summary', '') NOT LIKE '%[DRY RUN]%'
      AND (${project ?? null}::text IS NULL OR d.project = ${project ?? null})
    ORDER BY GREATEST(d.created_at, d.resolved_at, er.completed_at) DESC NULLS LAST
    LIMIT 200
  `) as Array<{
    decision_id: string;
    project: string;
    type: string;
    risk: "low" | "medium" | "high";
    created_at: Date;
    resolved_at: Date | null;
    summary: string;
    proposer_role: string | null;
    proposer_display_name: string | null;
    has_security_review: boolean;
    has_devils_advocate: boolean;
    column: SprintColumn | null;
    pr_url: string | null;
    pr_number: number | null;
    ci_conclusion: string | null;
    stage_since: Date | null;
  }>;

  const now = Date.now();
  const cards: SprintCard[] = rows
    .filter((r) => r.column !== null)
    .map((r) => {
      const stageSince = (r.stage_since ?? r.created_at).getTime();
      const ageMin = Math.max(0, (now - stageSince) / 60_000);
      const stalled =
        (r.column === "awaiting_you" && ageMin > 24 * 60) ||
        (r.column === "in_progress" && r.ci_conclusion === "failure" && ageMin > 6 * 60) ||
        (r.column === "approved" && ageMin > 30); // no engineer pickup yet
      const can_auto_merge =
        r.column === "review" &&
        r.risk === "low" &&
        r.ci_conclusion === "success" &&
        r.pr_url !== null &&
        r.pr_number !== null;
      const proposerRole = r.proposer_role ?? "system";
      return {
        decision_id: r.decision_id,
        project: r.project,
        column: r.column as SprintColumn,
        type: r.type,
        risk: r.risk,
        summary: r.summary,
        proposer_role: r.proposer_role,
        proposer_display_name: r.proposer_display_name,
        avatar_seed: `${proposerRole}@${r.project}`,
        pr_url: r.pr_url,
        pr_number: r.pr_number,
        ci_conclusion: r.ci_conclusion,
        age_minutes: Math.round(ageMin),
        stalled,
        has_security_review: r.has_security_review,
        has_devils_advocate: r.has_devils_advocate,
        can_auto_merge,
      };
    });

  return { projects, cards };
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
