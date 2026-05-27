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
  type AgileArtifact,
  type AgilePanel,
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
  SHARED_ENGINEERING_POOL,
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
      dn.display_name,
      COALESCE(ld.summary, r.last_event) AS last_output,
      live.run_id AS live_run_id,
      live.crew AS live_crew,
      live.project AS live_project,
      live.decision_id AS live_decision_id,
      live.decision_summary AS live_decision_summary,
      live.started_at AS live_started_at
    FROM recent r
    LEFT JOIN cost_today c USING (project, role)
    LEFT JOIN display_names dn USING (project, role)
    LEFT JOIN LATERAL (
      SELECT payload->>'summary' AS summary
      FROM decisions d
      WHERE d.id::text = r.last_decision_id
      LIMIT 1
    ) ld ON TRUE
    -- Most recent crew_started for this agent (last 10 min, no finish/fail).
    -- "For this agent" means either: the activity_log row's role column
    -- matches exactly, OR the role is listed in the payload's agents array.
    -- Shared agents (r.project IS NULL) match any project; per-project agents
    -- only match their own project's runs.
    LEFT JOIN LATERAL (
      SELECT
        s.run_id,
        s.crew,
        s.project,
        s.ts AS started_at,
        s.decision_id,
        (SELECT payload->>'summary' FROM decisions WHERE id::text = s.decision_id LIMIT 1) AS decision_summary
      FROM activity_log s
      WHERE s.event = 'crew_started'
        AND s.ts > NOW() - INTERVAL '10 minutes'
        AND (r.project IS NULL OR s.project = r.project)
        AND (
          s.role = r.role
          OR EXISTS (
            SELECT 1
            FROM jsonb_array_elements_text(COALESCE(s.payload->'agents', '[]'::jsonb)) ag
            WHERE ag = r.role
          )
        )
        AND NOT EXISTS (
          SELECT 1 FROM activity_log f
          WHERE f.run_id = s.run_id
            AND f.event IN ('crew_finished', 'crew_failed')
        )
      ORDER BY s.ts DESC
      LIMIT 1
    ) live ON TRUE
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
    last_output: string | null;
    live_run_id: string | null;
    live_crew: string | null;
    live_project: string | null;
    live_decision_id: string | null;
    live_decision_summary: string | null;
    live_started_at: Date | null;
  }>;

  const recentEventRows = (await s`
    WITH agent_events AS (
      SELECT project, role, ts, event, decision_id, payload
      FROM activity_log
      WHERE ts > NOW() - INTERVAL '30 days' AND role IS NOT NULL
      UNION ALL
      SELECT
        al.project,
        agent_role AS role,
        al.ts,
        al.event,
        al.decision_id,
        al.payload
      FROM activity_log al
      CROSS JOIN LATERAL jsonb_array_elements_text(
        COALESCE(al.payload->'agents', '[]'::jsonb)
      ) AS agent_role
      WHERE al.ts > NOW() - INTERVAL '30 days'
        AND al.payload ? 'agents'
    ),
    ranked AS (
      SELECT
        project,
        role,
        ts,
        event,
        decision_id,
        payload,
        ROW_NUMBER() OVER (PARTITION BY project, role ORDER BY ts DESC) AS rn
      FROM agent_events
      WHERE event IS NOT NULL
    )
    SELECT
      ranked.project, ranked.role, ranked.ts, ranked.event, ranked.decision_id, ranked.payload,
      (SELECT payload->>'summary' FROM decisions WHERE id::text = ranked.decision_id LIMIT 1)
        AS decision_summary
    FROM ranked
    WHERE rn <= 5
    ORDER BY project NULLS FIRST, role, ts DESC
  `) as Array<{
    project: string | null;
    role: string;
    ts: Date;
    event: string;
    decision_id: string | null;
    payload: Record<string, unknown> | null;
    decision_summary: string | null;
  }>;

  const recentByKey = new Map<string, AgentState["recent_events"]>();
  for (const event of recentEventRows) {
    const key = `${event.role}@${event.project ?? "shared"}`;
    const list = recentByKey.get(key) ?? [];
    list.push({
      ts: event.ts.toISOString(),
      event: event.event,
      sentence: sentenceForAgentEvent(
        event.role, event.project, event.event, event.decision_summary,
      ),
      decision_id: event.decision_id,
      decision_summary: event.decision_summary,
      pr_url: typeof event.payload?.["pr_url"] === "string" ? event.payload["pr_url"] : null,
    });
    recentByKey.set(key, list);
  }

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
  for (const role of SHARED_EXECUTIVE) {
    configured.push({ project: null, role, seats: 1 });
  }
  for (const { role, seats } of SHARED_SPECIALIST) {
    configured.push({ project: null, role, seats });
  }
  for (const { role, seatsPerProject } of SHARED_ENGINEERING_POOL) {
    configured.push({
      project: null,
      role,
      seats: Math.max(1, Math.ceil(seatsPerProject * projects.length)),
    });
  }
  for (const role of AUDIT) {
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
      last_output: summarizeAgentOutput(r?.last_output ?? null, r?.last_event ?? null),
      last_decision_id: r?.last_decision_id ?? null,
      in_flight: Boolean(r?.in_flight) || liveRunFromRow(r) !== null,
      errored: Boolean(r?.errored_recently),
      cost_today_usd: r?.cost_today_usd ?? 0,
      recent_events: recentByKey.get(key) ?? [],
      live_run: liveRunFromRow(r),
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
      last_output: summarizeAgentOutput(r.last_output, r.last_event),
      last_decision_id: r.last_decision_id,
      in_flight: Boolean(r.in_flight) || liveRunFromRow(r) !== null,
      errored: Boolean(r.errored_recently),
      cost_today_usd: r.cost_today_usd,
      recent_events: recentByKey.get(key) ?? [],
      live_run: liveRunFromRow(r),
    });
  }

  return merged;
}

function liveRunFromRow(
  r: {
    live_run_id: string | null;
    live_crew: string | null;
    live_project: string | null;
    live_decision_id: string | null;
    live_decision_summary: string | null;
    live_started_at: Date | null;
  } | undefined,
): AgentState["live_run"] {
  if (!r || !r.live_run_id || !r.live_crew || !r.live_started_at) return null;
  return {
    run_id: r.live_run_id,
    crew: r.live_crew,
    project: r.live_project,
    decision_id: r.live_decision_id,
    decision_summary: r.live_decision_summary,
    started_at: r.live_started_at.toISOString(),
  };
}

function summarizeAgentOutput(output: string | null, fallback: string | null): string | null {
  const text = (output ?? fallback ?? "").trim();
  if (!text) return null;
  const clean = text.replace(/\s+/g, " ");
  return clean.length > 96 ? `${clean.slice(0, 95)}…` : clean;
}

function sentenceForAgentEvent(
  role: string,
  project: string | null,
  event: string,
  decisionSummary: string | null,
): string {
  const who = project ? `${prettyAgentRole(role)} @ ${project}` : prettyAgentRole(role);
  // Trim long summaries to keep the recent-activity list scannable.
  const on = decisionSummary
    ? ` on "${decisionSummary.length > 64 ? `${decisionSummary.slice(0, 63)}…` : decisionSummary}"`
    : "";
  switch (event) {
    case "crew_started":
      return `${who} started work${on}`;
    case "crew_finished":
      return `${who} finished${on}`;
    case "crew_failed":
      return `${who} failed${on}`;
    case "crew_checkin":
      return `${who} checked in as available`;
    case "decision_submitted":
      return `${who} proposed work${on}`;
    case "decision_resolved":
      return `${who} resolved${on || " a Decision"}`;
    case "pr_opened":
      return `${who} opened a PR${on}`;
    case "pr_merged":
      return `${who} merged a PR${on}`;
    case "audit_finding_created":
      return `${who} raised an audit finding`;
    case "question_submitted":
      return `${who} asked for input`;
    default:
      return `${who} · ${event.replaceAll("_", " ")}${on}`;
  }
}

function prettyAgentRole(role: string): string {
  return role
    .split("_")
    .map((part) => `${part.slice(0, 1).toUpperCase()}${part.slice(1)}`)
    .join(" ");
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
  windowMinutes?: number;
}): Promise<ActivityEvent[]> {
  const s = sql();
  const limit = Math.min(opts.limit ?? 100, 500);
  const sinceId = opts.sinceId ?? 0;
  const windowMinutes = Math.min(Math.max(opts.windowMinutes ?? 60, 1), 24 * 60);

  // Filter clauses are stitched conditionally so we don't send empty SQL.
  const rows = (await s`
    SELECT
      al.id::int8 AS id,
      al.ts,
      al.event,
      al.project,
      al.role,
      al.decision_id,
      d.payload->>'summary' AS decision_summary,
      al.crew,
      al.run_id,
      al.error,
      al.payload
    FROM activity_log al
    LEFT JOIN decisions d ON d.id::text = al.decision_id
    WHERE al.id > ${sinceId}
      AND al.ts > NOW() - (${windowMinutes}::text || ' minutes')::interval
      AND (${opts.project ?? null}::text IS NULL OR al.project = ${opts.project ?? null})
      AND (${opts.role ?? null}::text IS NULL OR al.role = ${opts.role ?? null})
      AND (${opts.event ?? null}::text IS NULL OR al.event = ${opts.event ?? null})
    ORDER BY al.id DESC
    LIMIT ${limit}
  `) as Array<{
    id: number | bigint;
    ts: Date;
    event: string;
    project: string | null;
    role: string | null;
    decision_id: string | null;
    decision_summary: string | null;
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
    decision_summary: r.decision_summary,
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
    decision_summary: null,
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

import {
  type SprintBoard,
  type SprintCard,
  type SprintColumn,
  type SprintReviewer,
  type SprintReviewStatus,
  type SprintWindow,
  type Task,
  type AgentMemory,
  StructuredSprintPlanSchema,
} from "./schemas";

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
export async function listSprintBoard(
  project?: string,
  window: SprintWindow = "this_week",
): Promise<SprintBoard> {
  const s = sql();
  const { since, until } = sprintWindowBounds(window);

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
      COALESCE((d.payload->>'sprint_number')::int, d.sprint_number) AS sprint_number,
      COALESCE(d.structured_plan, d.payload->'structured_plan') AS structured_plan,
      CASE
        WHEN d.payload->>'priority' IN ('p1', 'p2', 'p3') THEN d.payload->>'priority'
        WHEN lower(COALESCE(d.payload->>'requested_by_role', '')) IN (
          'ceo', 'cto', 'md', 'managing_director', 'chair', 'board',
          'chief_product_officer', 'coo'
        ) THEN 'p1'
        WHEN lower(COALESCE(d.payload->>'requested_by_role', '')) IN (
          'principal', 'principal_engineer', 'pm', 'product_manager',
          'portfolio_owner', 'security_champion', 'spokesperson',
          'pr_followup', 'pr_review_loop'
        ) THEN 'p2'
        ELSE 'p3'
      END AS priority,
      CASE
        WHEN d.payload ? 'expedited' THEN COALESCE((d.payload->>'expedited')::boolean, false)
        WHEN lower(COALESCE(d.payload->>'requested_by_role', '')) IN (
          'ceo', 'cto', 'md', 'managing_director', 'chair', 'board',
          'chief_product_officer', 'coo', 'principal', 'principal_engineer',
          'pm', 'product_manager', 'portfolio_owner', 'security_champion',
          'spokesperson', 'pr_followup', 'pr_review_loop'
        ) THEN true
        ELSE false
      END AS expedited,
      d.payload->>'requested_by_role' AS requested_by_role,
      d.payload->>'proposer_role' AS proposer_role,
      d.payload->>'proposer_display_name' AS proposer_display_name,
      (d.payload ? 'security_review') AS has_security_review,
      (d.payload ? 'critique') AS has_devils_advocate,
      CASE
        WHEN d.status = 'pending' THEN 'awaiting_you'
        WHEN d.status IN ('rejected', 'timed_out') THEN NULL
        WHEN d.status = 'executed' AND er.pr_state = 'merged' THEN 'done'
        WHEN d.status = 'executed' AND er.pr_state = 'closed' THEN 'done'
        WHEN d.status = 'executed' AND er.payload->>'ci_conclusion' = 'failure' THEN 'in_progress'
        WHEN d.status = 'executed' AND er.payload->>'ci_conclusion' = 'pending' THEN 'in_progress'
        WHEN d.status = 'executed' AND er.payload->>'ci_conclusion' = 'success' THEN 'review'
        WHEN d.status = 'executed' THEN 'review'
        WHEN d.status = 'approved' AND er.decision_id IS NULL THEN 'approved'
        WHEN d.status = 'approved' THEN 'in_progress'
        ELSE 'backlog'
      END AS column,
      er.pr_url,
      er.pr_state,
      (er.payload->>'pr_number')::int AS pr_number,
      er.payload->>'ci_conclusion' AS ci_conclusion,
      er.payload->>'review_status' AS explicit_review_status,
      er.payload->'reviewers' AS explicit_reviewers,
      -- Per-PR iteration counter. Org repo renamed followup_attempts to
      -- iteration_count in the pr-lifecycle-resilience change (Phase 3).
      -- Reads tolerate both keys until every record has been re-saved on
      -- the new schema.
      COALESCE(
        (er.payload->>'iteration_count')::int,
        (er.payload->>'followup_attempts')::int,
        0
      ) AS iteration_count,
      er.payload->>'last_failure_kind' AS last_failure_kind,
      NULLIF(er.payload->>'last_followup_at', '')::timestamptz AS last_followup_at,
      NULLIF(er.payload->>'qa_review_posted_at', '')::timestamptz AS qa_review_posted_at,
      COALESCE((er.payload->>'operator_comment_posted')::boolean, false) AS operator_comment_posted,
      er.payload->>'merge_blocked_reason' AS merge_blocked_reason,
      er.payload->>'superseded_by_pr_url' AS superseded_by_pr_url,
      NULLIF(er.payload->>'superseded_at', '')::timestamptz AS superseded_at,
      NULLIF(er.payload->>'human_handoff_posted_at', '')::timestamptz AS human_handoff_posted_at,
      lc.crew AS live_crew_name,
      lc.started_at AS live_crew_started_at,
      lc.agents AS live_crew_agents,
      lc.run_id AS live_crew_run_id,
      GREATEST(d.created_at, d.resolved_at, er.completed_at) AS stage_since
    FROM decisions d
    LEFT JOIN engineer_runs er ON er.decision_id = d.id::text
    LEFT JOIN LATERAL (
      -- Most recent crew_started for this Decision with no matching
      -- crew_finished/crew_failed in the last 10 minutes. Mirrors the
      -- Python RUNNING_WINDOW_SECONDS = 10*60 invariant in activity.py.
      SELECT
        s.crew,
        s.ts AS started_at,
        COALESCE(s.payload->'agents', '[]'::jsonb) AS agents,
        s.run_id
      FROM activity_log s
      WHERE s.decision_id = d.id::text
        AND s.event = 'crew_started'
        AND s.ts > NOW() - INTERVAL '10 minutes'
        AND NOT EXISTS (
          SELECT 1 FROM activity_log f
          WHERE f.run_id = s.run_id
            AND f.event IN ('crew_finished', 'crew_failed')
        )
      ORDER BY s.ts DESC
      LIMIT 1
    ) lc ON TRUE
    WHERE COALESCE(d.payload->>'summary', '') NOT LIKE '%[DRY RUN]%'
      AND (${project ?? null}::text IS NULL OR d.project = ${project ?? null})
      AND (
        ${since}::timestamptz IS NULL
        OR GREATEST(d.created_at, d.resolved_at, er.completed_at) >= ${since}
      )
      AND (
        ${until}::timestamptz IS NULL
        OR GREATEST(d.created_at, d.resolved_at, er.completed_at) < ${until}
      )
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
    sprint_number: number | null;
    structured_plan: unknown;
    priority: "p1" | "p2" | "p3";
    expedited: boolean;
    requested_by_role: string | null;
    proposer_role: string | null;
    proposer_display_name: string | null;
    has_security_review: boolean;
    has_devils_advocate: boolean;
    column: SprintColumn | null;
    pr_url: string | null;
    pr_state: string | null;
    pr_number: number | null;
    ci_conclusion: string | null;
    explicit_review_status: string | null;
    explicit_reviewers: unknown;
    iteration_count: number;
    last_failure_kind: string | null;
    last_followup_at: Date | null;
    qa_review_posted_at: Date | null;
    operator_comment_posted: boolean;
    merge_blocked_reason: string | null;
    superseded_by_pr_url: string | null;
    superseded_at: Date | null;
    human_handoff_posted_at: Date | null;
    live_crew_name: string | null;
    live_crew_started_at: Date | null;
    live_crew_agents: unknown;
    live_crew_run_id: string | null;
    stage_since: Date | null;
  }>;

  const now = Date.now();
  const taskRows = await listTasksForDecisions(rows.map((r) => r.decision_id));
  const tasksByDecision = new Map<string, Task[]>();
  for (const task of taskRows) {
    const arr = tasksByDecision.get(task.decision_id) ?? [];
    arr.push(task);
    tasksByDecision.set(task.decision_id, arr);
  }
  const cards: SprintCard[] = rows
    .filter((r) => r.column !== null)
    .map((r) => {
      const stageSince = (r.stage_since ?? r.created_at).getTime();
      const ageMin = Math.max(0, (now - stageSince) / 60_000);
      const liveCrew = r.live_crew_name && r.live_crew_started_at && r.live_crew_run_id
        ? {
            crew: r.live_crew_name,
            started_at: r.live_crew_started_at.toISOString(),
            agents: Array.isArray(r.live_crew_agents)
              ? r.live_crew_agents.map(String)
              : [],
            run_id: r.live_crew_run_id,
          }
        : null;
      // An active crew on an "approved" card means the engineer crew has
      // already picked it up — surface it as in_progress so the operator
      // sees the work move out of the queue immediately, not after the
      // engineer_runs row is persisted at the end of the run.
      const column: SprintColumn =
        liveCrew && r.column === "approved" ? "in_progress" : (r.column as SprintColumn);
      const stalled =
        !liveCrew && (
          (column === "awaiting_you" && ageMin > 24 * 60) ||
          (column === "in_progress" && r.ci_conclusion === "failure" && ageMin > 6 * 60) ||
          (column === "approved" && r.expedited && ageMin > 15) ||
          (column === "approved" && ageMin > 6.5 * 60)
        );
      const can_auto_merge =
        r.column === "review" &&
        r.risk === "low" &&
        r.ci_conclusion === "success" &&
        r.pr_url !== null &&
        r.pr_number !== null;
      const proposerRole = r.proposer_role ?? "system";
      const review = sprintReviewState({
        column: r.column,
        ci_conclusion: r.ci_conclusion,
        explicit_review_status: r.explicit_review_status,
        explicit_reviewers: r.explicit_reviewers,
        merge_blocked_reason: r.merge_blocked_reason,
        superseded_by_pr_url: r.superseded_by_pr_url,
        superseded_at: r.superseded_at,
        human_handoff_posted_at: r.human_handoff_posted_at,
        iteration_count: r.iteration_count,
        qa_review_posted_at: r.qa_review_posted_at,
        operator_comment_posted: r.operator_comment_posted,
        pr_state: r.pr_state,
        can_auto_merge,
      });
      return {
        decision_id: r.decision_id,
        project: r.project,
        column,
        type: r.type,
        risk: r.risk,
        sprint_number: r.sprint_number,
        structured_plan: parseStructuredPlan(r.structured_plan),
        tasks: tasksByDecision.get(r.decision_id) ?? [],
        priority: r.priority,
        expedited: r.expedited,
        requested_by_role: r.requested_by_role,
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
        review_status: review.status,
        review_status_label: review.label,
        crew_last_action: review.lastAction,
        reviewers: review.reviewers,
        iteration_count: r.iteration_count,
        last_failure_kind: r.last_failure_kind,
        last_followup_at: r.last_followup_at ? r.last_followup_at.toISOString() : null,
        qa_review_posted_at: r.qa_review_posted_at ? r.qa_review_posted_at.toISOString() : null,
        operator_comment_posted: r.operator_comment_posted,
        merge_blocked_reason: r.merge_blocked_reason,
        human_handoff_posted_at: r.human_handoff_posted_at
          ? r.human_handoff_posted_at.toISOString()
          : null,
        live_crew: liveCrew,
      };
    });

  return { projects, cards };
}

function parseStructuredPlan(raw: unknown): SprintCard["structured_plan"] {
  if (!raw) return null;
  const parsed = StructuredSprintPlanSchema.safeParse(raw);
  return parsed.success ? parsed.data : null;
}

async function listTasksForDecisions(decisionIds: string[]): Promise<Task[]> {
  if (decisionIds.length === 0) return [];
  const s = sql();
  const rows = (await s`
    SELECT
      id::text,
      decision_id::text,
      project,
      sprint_number,
      category,
      title,
      description,
      acceptance_criteria,
      owner_role,
      owner_agent_id,
      owner_display_name,
      estimated_effort,
      status,
      pr_url,
      pr_number,
      created_at,
      updated_at,
      completed_at,
      payload
    FROM tasks
    WHERE decision_id::text = ANY(${decisionIds})
    ORDER BY created_at ASC
  `) as Array<{
    id: string;
    decision_id: string;
    project: string;
    sprint_number: number | null;
    category: Task["category"];
    title: string;
    description: string;
    acceptance_criteria: string | null;
    owner_role: string;
    owner_agent_id: string | null;
    owner_display_name: string | null;
    estimated_effort: Task["estimated_effort"];
    status: Task["status"];
    pr_url: string | null;
    pr_number: number | null;
    created_at: Date;
    updated_at: Date;
    completed_at: Date | null;
    payload: Record<string, unknown> | null;
  }>;
  return rows.map(rowToTask);
}

export async function listTasksForDecision(decisionId: string): Promise<Task[]> {
  return listTasksForDecisions([decisionId]);
}

export async function listTasksForProject(project: string, sprintNumber?: number): Promise<Task[]> {
  const s = sql();
  const rows = (await s`
    SELECT
      id::text,
      decision_id::text,
      project,
      sprint_number,
      category,
      title,
      description,
      acceptance_criteria,
      owner_role,
      owner_agent_id,
      owner_display_name,
      estimated_effort,
      status,
      pr_url,
      pr_number,
      created_at,
      updated_at,
      completed_at,
      payload
    FROM tasks
    WHERE project = ${project}
      AND (${sprintNumber ?? null}::int IS NULL OR sprint_number = ${sprintNumber ?? null})
    ORDER BY sprint_number DESC NULLS LAST, created_at ASC
    LIMIT 200
  `) as Parameters<typeof rowToTask>[0][];
  return rows.map(rowToTask);
}

export async function listAgentMemory(
  agentId: string,
  includeCold = false,
): Promise<AgentMemory[]> {
  const s = sql();
  const rows = (await s`
    SELECT
      id::text,
      agent_id,
      sprint_number,
      decision_id::text,
      task_id::text,
      pr_url,
      event,
      summary,
      details,
      created_at,
      tier
    FROM agent_memory
    WHERE agent_id = ${agentId}
      AND (${includeCold}::boolean OR tier = 'hot')
    ORDER BY created_at DESC
    LIMIT 50
  `) as Array<{
    id: string;
    agent_id: string;
    sprint_number: number | null;
    decision_id: string | null;
    task_id: string | null;
    pr_url: string | null;
    event: string;
    summary: string;
    details: string | null;
    created_at: Date;
    tier: "hot" | "cold";
  }>;
  return rows.map((r) => ({
    ...r,
    created_at: r.created_at.toISOString(),
  }));
}

function rowToTask(row: {
  id: string;
  decision_id: string;
  project: string;
  sprint_number: number | null;
  category: Task["category"];
  title: string;
  description: string;
  acceptance_criteria: string | null;
  owner_role: string;
  owner_agent_id: string | null;
  owner_display_name: string | null;
  estimated_effort: Task["estimated_effort"];
  status: Task["status"];
  pr_url: string | null;
  pr_number: number | null;
  created_at: Date;
  updated_at: Date;
  completed_at: Date | null;
  payload: Record<string, unknown> | null;
}): Task {
  return {
    ...row,
    acceptance_criteria: row.acceptance_criteria ?? "",
    created_at: row.created_at.toISOString(),
    updated_at: row.updated_at.toISOString(),
    completed_at: row.completed_at ? row.completed_at.toISOString() : null,
    payload: row.payload ?? {},
  };
}

function sprintWindowBounds(window: SprintWindow): {
  since: Date | null;
  until: Date | null;
} {
  const now = new Date();
  const startOfToday = new Date(now);
  startOfToday.setHours(0, 0, 0, 0);
  const day = startOfToday.getDay();
  const daysSinceMonday = (day + 6) % 7;
  const thisWeek = new Date(startOfToday);
  thisWeek.setDate(startOfToday.getDate() - daysSinceMonday);

  if (window === "this_week") {
    return { since: thisWeek, until: null };
  }
  if (window === "last_week") {
    const lastWeek = new Date(thisWeek);
    lastWeek.setDate(thisWeek.getDate() - 7);
    return { since: lastWeek, until: thisWeek };
  }
  if (window === "last_30d") {
    const since = new Date(now);
    since.setDate(now.getDate() - 30);
    return { since, until: null };
  }
  if (window === "last_90d") {
    const since = new Date(now);
    since.setDate(now.getDate() - 90);
    return { since, until: null };
  }
  return { since: null, until: null };
}

function sprintReviewState(input: {
  column: SprintColumn | null;
  ci_conclusion: string | null;
  explicit_review_status: string | null;
  explicit_reviewers: unknown;
  merge_blocked_reason: string | null;
  superseded_by_pr_url: string | null;
  superseded_at: Date | null;
  human_handoff_posted_at: Date | null;
  iteration_count: number;
  qa_review_posted_at: Date | null;
  operator_comment_posted: boolean;
  pr_state: string | null;
  can_auto_merge: boolean;
}): {
  status: SprintReviewStatus;
  label: string;
  lastAction: string;
  reviewers: SprintReviewer[];
} {
  const explicit = explicitSprintReviewState({
    rawStatus: input.explicit_review_status,
    rawReviewers: input.explicit_reviewers,
    mergeBlockedReason: input.merge_blocked_reason,
    humanHandoffPostedAt: input.human_handoff_posted_at,
  });
  if (explicit) return explicit;

  const creatorStatus =
    input.column === "approved" || input.column === "backlog" || input.column === "awaiting_you"
      ? "waiting"
      : "approved";
  const reviewers: SprintReviewer[] = [
    {
      role: "engineer",
      label: "Creator",
      status: creatorStatus,
      detail: creatorStatus === "approved" ? "PR opened" : "waiting for pickup",
    },
    {
      role: "ttl",
      label: "TTL",
      status: input.operator_comment_posted ? "approved" : input.column === "review" ? "reviewing" : "waiting",
      detail: input.operator_comment_posted ? "briefed PR" : "will review PR shape",
    },
    {
      role: "qa_engineer",
      label: "QA",
      status: input.qa_review_posted_at
        ? "approved"
        : input.ci_conclusion === "failure"
          ? "changes_requested"
          : input.ci_conclusion === "success"
            ? "reviewing"
            : "waiting",
      detail: input.qa_review_posted_at
        ? "QA comment posted"
        : input.ci_conclusion === "failure"
          ? "CI needs a fix"
          : input.ci_conclusion === "success"
            ? "reviewing green PR"
            : "waiting on CI",
    },
  ];

  if (input.pr_state === "merged") {
    return {
      status: "merged",
      label: "Merged",
      lastAction: "The PR has shipped.",
      reviewers: reviewers.map((r) => ({ ...r, status: "approved" })),
    };
  }

  if (input.pr_state === "closed") {
    const superseded =
      input.superseded_at !== null ||
      input.superseded_by_pr_url !== null ||
      input.iteration_count > 0;
    return {
      status: superseded ? "superseded" : "closed",
      label: superseded ? "Superseded" : "Closed",
      lastAction: superseded
        ? `This PR was closed after a replacement path was queued${input.superseded_by_pr_url ? `: ${input.superseded_by_pr_url}` : "."}`
        : "This PR was closed without merging.",
      reviewers: reviewers.map((r) => ({
        ...r,
        status: r.status === "changes_requested" ? "approved" : r.status,
      })),
    };
  }

  if (input.iteration_count > 0 && input.ci_conclusion === "failure") {
    return {
      status: "fix_queued",
      label: "Fix queued",
      lastAction: `PR follow-up queued ${input.iteration_count} fix attempt${input.iteration_count === 1 ? "" : "s"}.`,
      reviewers,
    };
  }

  if (input.ci_conclusion === "failure") {
    return {
      status: "changes_requested",
      label: "Changes requested",
      lastAction: "CI is red; the crew needs to open or queue a correction.",
      reviewers,
    };
  }

  if (input.ci_conclusion === "pending") {
    return {
      status: "ci_running",
      label: "CI running",
      lastAction: "Checks are still running before peer review finishes.",
      reviewers,
    };
  }

  if (input.can_auto_merge && input.qa_review_posted_at) {
    return {
      status: "crew_approved",
      label: "Crew approved",
      lastAction: "Creator and reviewers are satisfied; merge is available.",
      reviewers,
    };
  }

  if (input.column === "review" && input.ci_conclusion === "success") {
    return {
      status: "crew_reviewing",
      label: "Crew reviewing",
      lastAction: "CI is green; reviewers are doing the final pass.",
      reviewers,
    };
  }

  if (input.column === "review") {
    return {
      status: "needs_operator",
      label: "Needs operator",
      lastAction: "PR is open, but the crew does not have enough signal to merge.",
      reviewers,
    };
  }

  return {
    status: "not_started",
    label: "Not in review",
    lastAction: "Waiting for the PR to reach crew review.",
    reviewers,
  };
}

function explicitSprintReviewState(input: {
  rawStatus: string | null;
  rawReviewers: unknown;
  mergeBlockedReason: string | null;
  humanHandoffPostedAt: Date | null;
}): {
  status: SprintReviewStatus;
  label: string;
  lastAction: string;
  reviewers: SprintReviewer[];
} | null {
  const { rawStatus, rawReviewers, mergeBlockedReason, humanHandoffPostedAt } = input;
  if (!rawStatus || rawStatus === "not_started") return null;

  const reviewers = Array.isArray(rawReviewers)
    ? rawReviewers.map((r): SprintReviewer | null => {
        if (!r || typeof r !== "object") return null;
        const item = r as Record<string, unknown>;
        const role = String(item.role ?? "reviewer");
        const status = String(item.status ?? "assigned");
        return {
          role,
          label: String(item.display_name ?? role),
          status: mapReviewerStatus(status),
          detail: String(item.summary ?? mapReviewerDetail(status)),
        };
      }).filter((r): r is SprintReviewer => r !== null)
    : [];

  const status = mapReviewStatus(rawStatus);
  return {
    status,
    label: mapReviewLabel(status, rawStatus),
    lastAction: mapReviewLastAction(status, mergeBlockedReason, humanHandoffPostedAt),
    reviewers,
  };
}

function mapReviewerStatus(raw: string): SprintReviewer["status"] {
  switch (raw) {
    case "approved":
      return "approved";
    case "changes_requested":
      return "changes_requested";
    case "commented":
      return "reviewing";
    default:
      return "waiting";
  }
}

function mapReviewerDetail(raw: string): string {
  switch (raw) {
    case "approved":
      return "approved";
    case "changes_requested":
      return "requested changes";
    case "commented":
      return "commented";
    default:
      return "assigned";
  }
}

function mapReviewStatus(raw: string): SprintReviewStatus {
  switch (raw) {
    case "assigned":
    case "reviewing":
    case "creator_responded":
      return "crew_reviewing";
    case "changes_requested":
      return "changes_requested";
    case "crew_approved":
    case "merge_attempted":
      return "crew_approved";
    case "merge_blocked":
      return "needs_operator";
    case "merged":
      return "merged";
    case "superseded":
      return "superseded";
    case "closed":
      return "closed";
    default:
      return "not_started";
  }
}

function mapReviewLabel(status: SprintReviewStatus, raw: string): string {
  if (raw === "assigned") return "Review assigned";
  if (raw === "creator_responded") return "Creator responded";
  if (raw === "merge_attempted") return "Merge attempted";
  switch (status) {
    case "crew_reviewing":
      return "Crew reviewing";
    case "changes_requested":
      return "Changes requested";
    case "crew_approved":
      return "Crew approved";
    case "needs_operator":
      return "Ready for you";
    case "merged":
      return "Merged";
    case "superseded":
      return "Superseded";
    case "closed":
      return "Closed";
    default:
      return "Not in review";
  }
}

function mapReviewLastAction(
  status: SprintReviewStatus,
  mergeBlockedReason?: string | null,
  humanHandoffPostedAt?: Date | null,
): string {
  switch (status) {
    case "crew_reviewing":
      return "Internal reviewers are assigned and posting structured comments.";
    case "changes_requested":
      return "At least one crew reviewer requested changes.";
    case "crew_approved":
      return "Crew reviewers approved this PR.";
    case "needs_operator":
      return humanHandoffPostedAt
        ? `Crew approval is complete; branch protection needs the operator. ${mergeBlockedReason ?? ""}`.trim()
        : "Crew approval is complete; branch protection needs the operator.";
    case "merged":
      return "The PR has shipped.";
    case "superseded":
      return "The original PR was closed after a replacement PR took over.";
    case "closed":
      return "The PR was closed without merging.";
    default:
      return "Waiting for the PR to reach crew review.";
  }
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

// ---------- Agile cadence ----------

export async function listAgilePanel(project?: string): Promise<AgilePanel> {
  const s = sql();
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
    ORDER BY project
  `) as Array<{ project: string }>;
  const projects = projectRows.map((r) => r.project);

  const hasAgile = (await s`
    SELECT to_regclass('public.agile_rituals') IS NOT NULL AS ok
  `) as Array<{ ok: boolean }>;
  if (!hasAgile[0]?.ok) {
    return { projects, artifacts: [], pm_answers: [] };
  }

  const artifactRows = (await s`
    SELECT payload
    FROM agile_rituals
    WHERE (${project ?? null}::text IS NULL OR project = ${project ?? null})
    ORDER BY created_at DESC
    LIMIT 24
  `) as Array<{ payload: Record<string, unknown> }>;

  const artifacts: AgileArtifact[] = artifactRows.map(({ payload }) => ({
    id: String(payload.id ?? ""),
    project: String(payload.project ?? ""),
    ritual: String(payload.ritual ?? "scrum") as AgileArtifact["ritual"],
    summary: String(payload.summary ?? ""),
    blockers: Array.isArray(payload.blockers) ? payload.blockers.map(String) : [],
    next_actions: Array.isArray(payload.next_actions)
      ? payload.next_actions.map(String)
      : [],
    related_pr_urls: Array.isArray(payload.related_pr_urls)
      ? payload.related_pr_urls.map(String)
      : [],
    created_at: String(payload.created_at ?? new Date().toISOString()),
  }));

  const hasPm = (await s`
    SELECT to_regclass('public.pm_answers') IS NOT NULL AS ok
  `) as Array<{ ok: boolean }>;
  if (!hasPm[0]?.ok) {
    return { projects, artifacts, pm_answers: [] };
  }

  const answerRows = (await s`
    SELECT payload
    FROM pm_answers
    WHERE (${project ?? null}::text IS NULL OR project = ${project ?? null})
    ORDER BY created_at DESC
    LIMIT 12
  `) as Array<{ payload: Record<string, unknown> }>;

  return {
    projects,
    artifacts,
    pm_answers: answerRows.map(({ payload }) => ({
      id: String(payload.id ?? ""),
      project: String(payload.project ?? ""),
      question: String(payload.question ?? ""),
      answer: String(payload.answer ?? ""),
      citations: Array.isArray(payload.citations) ? payload.citations.map(String) : [],
      escalated_to:
        typeof payload.escalated_to === "string" ? payload.escalated_to : null,
      created_at: String(payload.created_at ?? new Date().toISOString()),
    })),
  };
}

// ---------- Crew transcripts ----------

import type { CrewTranscriptMessage } from "./schemas";

function _mapTranscriptRow(payload: Record<string, unknown>): CrewTranscriptMessage {
  return {
    id: String(payload.id ?? ""),
    run_id: String(payload.run_id ?? ""),
    project: String(payload.project ?? ""),
    crew: String(payload.crew ?? ""),
    agent_role: String(payload.agent_role ?? ""),
    agent_display_name:
      payload.agent_display_name == null
        ? null
        : String(payload.agent_display_name),
    sequence:
      typeof payload.sequence === "number"
        ? payload.sequence
        : Number(payload.sequence ?? 0),
    role_in_conversation:
      (String(payload.role_in_conversation ?? "other") as
        | "pitch"
        | "rebuttal"
        | "synthesis"
        | "review"
        | "task_output"
        | "other"),
    content: String(payload.content ?? ""),
    created_at: String(payload.created_at ?? new Date().toISOString()),
  };
}

export async function listTranscriptByRun(
  runId: string,
): Promise<CrewTranscriptMessage[]> {
  const s = sql();
  const hasTable = (await s`
    SELECT to_regclass('public.crew_transcripts') IS NOT NULL AS ok
  `) as Array<{ ok: boolean }>;
  if (!hasTable[0]?.ok) return [];
  const rows = (await s`
    SELECT payload FROM crew_transcripts
    WHERE run_id = ${runId}
    ORDER BY sequence ASC
  `) as Array<{ payload: Record<string, unknown> }>;
  return rows.map(({ payload }) => _mapTranscriptRow(payload));
}

export async function listTranscriptsForProject(
  project: string,
  limit = 50,
): Promise<CrewTranscriptMessage[]> {
  const s = sql();
  const hasTable = (await s`
    SELECT to_regclass('public.crew_transcripts') IS NOT NULL AS ok
  `) as Array<{ ok: boolean }>;
  if (!hasTable[0]?.ok) return [];
  const rows = (await s`
    SELECT payload FROM crew_transcripts
    WHERE project = ${project}
    ORDER BY created_at DESC
    LIMIT ${Math.min(Math.max(limit, 1), 200)}
  `) as Array<{ payload: Record<string, unknown> }>;
  return rows.map(({ payload }) => _mapTranscriptRow(payload));
}

// ---------- Meeting room (living-org-spaces Surface A) ----------

import type { MeetingDetail, MeetingSummary, MeetingTurn, Seat } from "./schemas";
import {
  FALLBACK_SEAT_POSITIONS,
  ritualFor,
  type SeatPosition,
} from "./meetings/rituals";

/**
 * Default look-back window for "what meetings happened recently?"
 *
 * Set to 48h per operator request 2026-05-27 — keep meetings on the page
 * for two days, then archive (drop from the default list). To see older
 * runs explicitly the caller can pass a longer windowMinutes.
 */
const MEETINGS_WINDOW_MINUTES_DEFAULT = 48 * 60;

/**
 * Window inside which the most recent turn is treated as "speaking now" for
 * the round-table halo indicator. Matches the Python-side
 * RUNNING_WINDOW_SECONDS / 60 in activity.py so the front-end speaker pulse
 * and back-end "live crew" detection don't disagree.
 */
const SPEAKING_NOW_WINDOW_MS = 10 * 60 * 1000;

/** Char budget for the truncated `content_preview` in the summary panel. */
const PREVIEW_CHARS = 280;

/**
 * Aggregate a list of crew_transcript payloads (one per turn) into the
 * round-table view: seats, latest turn, status, etc. Pure helper — does no IO.
 */
function _aggregateMeeting(args: {
  run_id: string;
  crew: string;
  project: string | null;
  decision_id: string | null;
  started_at: string;
  last_event_at: string;
  status: MeetingSummary["status"];
  turns: MeetingTurn[];
}): MeetingSummary {
  const ritual = ritualFor(args.crew);
  // Track each role's most recent turn so we can populate the chat-bubble
  // that floats above the agent's seat in the round-table.
  interface RoleMeta {
    display: string | null;
    lastTurnAt: string;
    lastTurnPreview: string | null;
    lastTurnSequence: number | null;
  }
  const seenRoles = new Map<string, RoleMeta>();
  for (const turn of args.turns) {
    const existing = seenRoles.get(turn.agent_role);
    if (!existing || turn.created_at > existing.lastTurnAt) {
      seenRoles.set(turn.agent_role, {
        display: turn.agent_display_name,
        lastTurnAt: turn.created_at,
        // Use the existing preview from the turn (already truncated by
        // _mapTurnRow). For the seat-tracking shim (roster rows without
        // content), preview is empty string — render as null.
        lastTurnPreview: turn.content_preview || turn.content_full || null,
        lastTurnSequence: turn.sequence > 0 ? turn.sequence : null,
      });
    }
  }

  // Build seats — start from the ritual's mapped roles, then fall back to
  // FALLBACK_SEAT_POSITIONS for any agent_roles that turned up in the
  // transcripts but weren't in the ritual's seat_layout.
  const seats: Seat[] = [];
  const usedPositions = new Set<SeatPosition>();
  const latestTurn = args.turns.length > 0 ? args.turns[args.turns.length - 1] : null;
  const speakingNow =
    latestTurn != null &&
    Date.now() - new Date(latestTurn.created_at).getTime() < SPEAKING_NOW_WINDOW_MS;

  for (const [role, meta] of seenRoles) {
    const mappedPos = ritual.seat_layout[role];
    let position: SeatPosition;
    if (mappedPos && !usedPositions.has(mappedPos)) {
      position = mappedPos;
    } else {
      const fallback = FALLBACK_SEAT_POSITIONS.find((p) => !usedPositions.has(p));
      position = fallback ?? "center";
    }
    usedPositions.add(position);
    seats.push({
      agent_role: role,
      agent_display_name: meta.display,
      seat_position: position,
      is_speaking_now: speakingNow && latestTurn?.agent_role === role,
      last_turn_preview: meta.lastTurnPreview,
      last_turn_sequence: meta.lastTurnSequence,
    });
  }
  // Stable order: ritual-mapped roles first (in ritual order), unknowns after.
  const ritualOrder = Object.keys(ritual.seat_layout);
  seats.sort((a, b) => {
    const ai = ritualOrder.indexOf(a.agent_role);
    const bi = ritualOrder.indexOf(b.agent_role);
    if (ai === -1 && bi === -1) return a.agent_role.localeCompare(b.agent_role);
    if (ai === -1) return 1;
    if (bi === -1) return -1;
    return ai - bi;
  });

  return {
    run_id: args.run_id,
    crew: args.crew,
    ritual_label: ritual.label,
    ritual_agenda: ritual.agenda,
    multi_agent: ritual.multi_agent,
    project: args.project,
    decision_id: args.decision_id,
    started_at: args.started_at,
    last_event_at: args.last_event_at,
    status: args.status,
    seats,
    latest_turn: latestTurn,
    total_turns: args.turns.length,
  };
}

/** Truncate to PREVIEW_CHARS on a clean word boundary; keep newlines visible. */
function _preview(content: string): string {
  const compact = content.trim();
  if (compact.length <= PREVIEW_CHARS) return compact;
  const cut = compact.slice(0, PREVIEW_CHARS);
  const lastSpace = cut.lastIndexOf(" ");
  return (lastSpace > PREVIEW_CHARS * 0.6 ? cut.slice(0, lastSpace) : cut) + "…";
}

function _mapTurnRow(payload: Record<string, unknown>): MeetingTurn {
  const full = String(payload.content ?? "");
  return {
    sequence:
      typeof payload.sequence === "number"
        ? payload.sequence
        : Number(payload.sequence ?? 0),
    agent_role: String(payload.agent_role ?? ""),
    agent_display_name:
      payload.agent_display_name == null ? null : String(payload.agent_display_name),
    role_in_conversation: (String(payload.role_in_conversation ?? "other") as
      | "pitch"
      | "rebuttal"
      | "synthesis"
      | "review"
      | "task_output"
      | "other"),
    content_preview: _preview(full),
    content_full: full,
    created_at: String(payload.created_at ?? new Date().toISOString()),
  };
}

/**
 * List recent meetings — one summary per crew run in the last `windowMinutes`.
 *
 * In-progress runs (those with a `crew_started` activity_log event but no
 * `crew_finished` / `crew_failed` within the window) are surfaced first,
 * followed by completed runs in reverse-chronological order.
 */
export async function listMeetings(opts?: {
  windowMinutes?: number;
  project?: string;
}): Promise<MeetingSummary[]> {
  const windowMinutes = opts?.windowMinutes ?? MEETINGS_WINDOW_MINUTES_DEFAULT;
  const project = opts?.project ?? null;
  const s = sql();
  const hasTable = (await s`
    SELECT to_regclass('public.crew_transcripts') IS NOT NULL AS ok
  `) as Array<{ ok: boolean }>;
  if (!hasTable[0]?.ok) return [];

  // One row per (run_id, crew, project) with the turns aggregated as JSON.
  // We also pull the matching crew_started / crew_finished events from
  // activity_log to derive status + canonical started_at.
  const rows = (await s`
    WITH run_window AS (
      SELECT
        ct.run_id,
        MIN(ct.crew) AS crew,
        MIN(ct.project) AS project,
        MIN(ct.created_at) AS first_turn_at,
        MAX(ct.created_at) AS last_turn_at,
        COUNT(*) AS turn_count
      FROM crew_transcripts ct
      WHERE ct.created_at > NOW() - (${windowMinutes}::int || ' minutes')::interval
        AND (${project}::text IS NULL OR ct.project = ${project}::text)
      GROUP BY ct.run_id
    ),
    run_lifecycle AS (
      SELECT
        run_id,
        MIN(ts) FILTER (WHERE event = 'crew_started') AS started_at,
        MAX(ts) FILTER (WHERE event IN ('crew_started','agent_spoke','crew_finished','crew_failed')) AS last_event_at,
        BOOL_OR(event = 'crew_finished') AS finished_ok,
        BOOL_OR(event = 'crew_failed') AS failed,
        MAX(decision_id) AS decision_id
      FROM activity_log
      WHERE ts > NOW() - (${windowMinutes}::int || ' minutes')::interval
        AND run_id IS NOT NULL
      GROUP BY run_id
    )
    SELECT
      rw.run_id,
      rw.crew,
      rw.project,
      COALESCE(rl.started_at, rw.first_turn_at) AS started_at,
      COALESCE(rl.last_event_at, rw.last_turn_at) AS last_event_at,
      rl.finished_ok,
      rl.failed,
      rl.decision_id,
      rw.turn_count
    FROM run_window rw
    LEFT JOIN run_lifecycle rl ON rl.run_id = rw.run_id
    ORDER BY
      CASE
        WHEN COALESCE(rl.finished_ok, false) OR COALESCE(rl.failed, false) THEN 1
        ELSE 0
      END,
      COALESCE(rl.last_event_at, rw.last_turn_at) DESC
    LIMIT 50
  `) as Array<{
    run_id: string;
    crew: string;
    project: string | null;
    started_at: Date;
    last_event_at: Date;
    finished_ok: boolean | null;
    failed: boolean | null;
    decision_id: string | null;
    turn_count: number;
  }>;
  if (rows.length === 0) return [];

  // Fetch the most recent turn per run for the latest-turn panel + the
  // speaker halo. Pulling all turns for the list view would be wasteful;
  // the detail endpoint is the place for that.
  const runIds = rows.map((r) => r.run_id);
  const latestTurns = (await s`
    SELECT DISTINCT ON (run_id) run_id, payload
    FROM crew_transcripts
    WHERE run_id = ANY(${runIds})
    ORDER BY run_id, sequence DESC
  `) as Array<{ run_id: string; payload: Record<string, unknown> }>;
  const latestByRun = new Map(latestTurns.map((t) => [t.run_id, _mapTurnRow(t.payload)]));

  // Pull the LATEST turn per (run_id, agent_role) so each seat in the
  // round-table has a populated chat-bubble. Without this, only the
  // single most-recent speaker has bubble content; every other seat
  // shows up with `last_turn_preview = null`.
  const seatLastTurns = (await s`
    SELECT DISTINCT ON (run_id, agent_role)
      run_id,
      agent_role,
      payload
    FROM crew_transcripts
    WHERE run_id = ANY(${runIds})
    ORDER BY run_id, agent_role, sequence DESC
  `) as Array<{ run_id: string; agent_role: string; payload: Record<string, unknown> }>;
  const rosterByRun = new Map<string, MeetingTurn[]>();
  for (const row of seatLastTurns) {
    const arr = rosterByRun.get(row.run_id) ?? [];
    arr.push(_mapTurnRow(row.payload));
    rosterByRun.set(row.run_id, arr);
  }

  return rows.map((row) => {
    const latest = latestByRun.get(row.run_id) ?? null;
    const seatTurns = rosterByRun.get(row.run_id) ?? [];
    // Compose the turn list the aggregator sees: every seat's last turn
    // (so each seat gets a populated bubble) PLUS the global latest turn
    // (so the speaker-halo math has a deterministic "most recent").
    const turnsForAgg: MeetingTurn[] = [...seatTurns];
    if (latest && !turnsForAgg.some((t) => t.sequence === latest.sequence)) {
      turnsForAgg.push(latest);
    }
    const status: MeetingSummary["status"] = row.failed
      ? "failed"
      : row.finished_ok
        ? "completed"
        : "in_progress";
    const summary = _aggregateMeeting({
      run_id: row.run_id,
      crew: row.crew,
      project: row.project,
      decision_id: row.decision_id,
      started_at: row.started_at.toISOString(),
      last_event_at: row.last_event_at.toISOString(),
      status,
      turns: turnsForAgg,
    });
    // Override latest_turn + total_turns from the authoritative DB counts —
    // _aggregateMeeting otherwise counts `turnsForAgg` which is just the
    // latest + roster-tracking shim.
    return {
      ...summary,
      latest_turn: latest,
      total_turns: row.turn_count,
    };
  });
}

/** Full meeting detail — every turn in conversation order. */
export async function getMeeting(runId: string): Promise<MeetingDetail | null> {
  const s = sql();
  const hasTable = (await s`
    SELECT to_regclass('public.crew_transcripts') IS NOT NULL AS ok
  `) as Array<{ ok: boolean }>;
  if (!hasTable[0]?.ok) return null;

  const turnRows = (await s`
    SELECT payload
    FROM crew_transcripts
    WHERE run_id = ${runId}
    ORDER BY sequence ASC
  `) as Array<{ payload: Record<string, unknown> }>;
  if (turnRows.length === 0) return null;

  const turns = turnRows.map(({ payload }) => _mapTurnRow(payload));

  // Crew + project come from the first turn (all rows for a run_id share these).
  const firstPayload = turnRows[0].payload;
  const crew = String(firstPayload.crew ?? "");
  const project = firstPayload.project == null ? null : String(firstPayload.project);

  // Lifecycle from activity_log.
  const lifecycle = (await s`
    SELECT
      MIN(ts) FILTER (WHERE event = 'crew_started') AS started_at,
      MAX(ts) FILTER (WHERE event IN ('crew_started','agent_spoke','crew_finished','crew_failed')) AS last_event_at,
      BOOL_OR(event = 'crew_finished') AS finished_ok,
      BOOL_OR(event = 'crew_failed') AS failed,
      MAX(decision_id) AS decision_id
    FROM activity_log
    WHERE run_id = ${runId}
  `) as Array<{
    started_at: Date | null;
    last_event_at: Date | null;
    finished_ok: boolean | null;
    failed: boolean | null;
    decision_id: string | null;
  }>;
  const lc = lifecycle[0] ?? {
    started_at: null,
    last_event_at: null,
    finished_ok: null,
    failed: null,
    decision_id: null,
  };

  const status: MeetingSummary["status"] = lc.failed
    ? "failed"
    : lc.finished_ok
      ? "completed"
      : "in_progress";

  const startedAt = (lc.started_at ?? new Date(turns[0].created_at)).toISOString();
  const lastEventAt = (
    lc.last_event_at ?? new Date(turns[turns.length - 1].created_at)
  ).toISOString();

  const summary = _aggregateMeeting({
    run_id: runId,
    crew,
    project,
    decision_id: lc.decision_id,
    started_at: startedAt,
    last_event_at: lastEventAt,
    status,
    turns,
  });

  return { ...summary, turns };
}
