/**
 * Shared zod schemas for the operator console API.
 *
 * Every route handler validates its response against one of these. Client
 * components import the inferred TS types — server and browser see the
 * exact same shape.
 */

import { z } from "zod";

// ---------- Agents ----------

export const AgentStateSchema = z.object({
  id: z.string(), // e.g. "engineer@AaaG#2"
  display_name: z.string().nullable(),
  role: z.string(),
  role_tier: z.enum(["executive", "engineering", "audit", "specialist"]),
  project: z.string().nullable(), // null for shared executives
  tier: z.string(), // model tier — opus/sonnet/haiku
  seats: z.number().int().positive(), // Number of seats for this role on this floor.
  last_event_at: z.string().datetime().nullable(),
  last_event: z.string().nullable(),
  last_output: z.string().nullable(),
  last_decision_id: z.string().nullable(),
  in_flight: z.boolean(),
  errored: z.boolean(),
  cost_today_usd: z.number(),
  recent_events: z.array(
    z.object({
      ts: z.string().datetime(),
      event: z.string(),
      sentence: z.string(),
      decision_id: z.string().nullable(),
      decision_summary: z.string().nullable(),
      pr_url: z.string().nullable(),
    })
  ),
  // Active crew assignment, populated when this agent appears in a
  // `crew_started` event (last 10 min) with no matching `crew_finished` /
  // `crew_failed`. Lets the Floor say "Engineer is working on
  // 'Fix CI failure'" instead of just "in_flight = true".
  live_run: z
    .object({
      run_id: z.string(),
      crew: z.string(),
      project: z.string().nullable(),
      decision_id: z.string().nullable(),
      decision_summary: z.string().nullable(),
      started_at: z.string().datetime(),
    })
    .nullable(),
});
export type AgentState = z.infer<typeof AgentStateSchema>;

// ---------- Agent profile (persistent identity) ----------

export const AgentStatsSchema = z.object({
  prs_opened: z.number(),
  prs_merged: z.number(),
  reviews_received: z.number(),
  blockers_hit: z.number(),
  last_active_at: z.string().nullable(),
});
export type AgentStats = z.infer<typeof AgentStatsSchema>;

export const AgentProfileSchema = z.object({
  agent_id: z.string(),
  role: z.string(),
  display_name: z.string().nullable(),
  persona: z.string(),
  joined_sprint: z.number().nullable(),
  specialties: z.array(z.string()),
  stats: AgentStatsSchema,
  updated_at: z.string(),
});
export type AgentProfile = z.infer<typeof AgentProfileSchema>;

// ---------- Work items ----------

export const WorkItemStageSchema = z.enum([
  "idea",
  "proposed",
  "awaiting_you",
  "approved",
  "coding",
  "ci",
  "review",
  "merged",
]);
export type WorkItemStage = z.infer<typeof WorkItemStageSchema>;

export const WorkItemSchema = z.object({
  decision_id: z.string(),
  project: z.string(),
  summary: z.string(),
  risk: z.enum(["low", "medium", "high"]),
  stage: WorkItemStageSchema,
  pr_url: z.string().nullable(),
  pr_number: z.number().nullable(),
  ci_conclusion: z.string().nullable(),
  stage_since: z.string().datetime(),
  stalled: z.boolean(),
});
export type WorkItem = z.infer<typeof WorkItemSchema>;

// ---------- Activity events ----------

export const ActivityEventSchema = z.object({
  id: z.number(),
  ts: z.string().datetime(),
  event: z.string(),
  project: z.string().nullable(),
  role: z.string().nullable(),
  decision_id: z.string().nullable(),
  decision_summary: z.string().nullable(),
  crew: z.string().nullable(),
  run_id: z.string().nullable(),
  error: z.string().nullable(),
  payload: z.record(z.string(), z.unknown()).nullable(),
});
export type ActivityEvent = z.infer<typeof ActivityEventSchema>;

// ---------- Cost ----------

export const CostSummarySchema = z.object({
  today_usd: z.number(),
  week_to_date_usd: z.number(),
  week_cap_usd: z.number(),
  fraction_of_week_cap: z.number(),
});
export type CostSummary = z.infer<typeof CostSummarySchema>;

// ---------- Questions ----------

export const QuestionSchema = z.object({
  id: z.string(),
  project: z.string(),
  asker_role: z.string(),
  asker_agent_id: z.string(),
  target_role: z.string(),
  question: z.string(),
  context: z.string().nullable(),
  related_decision_id: z.string().nullable(),
  related_pr_url: z.string().nullable(),
  status: z.enum(["open", "answered", "escalated", "cancelled"]),
  created_at: z.string().datetime(),
  escalated_at: z.string().datetime().nullable(),
});
export type Question = z.infer<typeof QuestionSchema>;

// ---------- Hero event (newest meaningful) ----------

/**
 * The single most attention-worthy recent event. Powers the page hero strip.
 * Falls back to ``null`` when there are no meaningful events at all.
 */
export const HeroEventSchema = z
  .object({
    ts: z.string().datetime(),
    event: z.string(),
    project: z.string().nullable(),
    role: z.string().nullable(),
    role_tier: z.enum(["executive", "engineering", "audit", "specialist"]),
    sentence: z.string(),
    deep_link_href: z.string().nullable(),
    deep_link_label: z.string().nullable(),
    avatar_seed: z.string(),
  })
  .nullable();
export type HeroEvent = z.infer<typeof HeroEventSchema>;

// ---------- Sprint Board ----------

export const SprintColumnSchema = z.enum([
  "backlog",
  "awaiting_you",
  "approved",
  "in_progress",
  "review",
  "done",
]);
export type SprintColumn = z.infer<typeof SprintColumnSchema>;

export const SprintWindowSchema = z.enum([
  "this_week",
  "last_week",
  "last_30d",
  "last_90d",
  "all",
]);
export type SprintWindow = z.infer<typeof SprintWindowSchema>;

export const SprintReviewStatusSchema = z.enum([
  "not_started",
  "ci_running",
  "changes_requested",
  "fix_queued",
  "crew_reviewing",
  "crew_approved",
  "needs_operator",
  "merged",
  "superseded",
  "closed",
]);
export type SprintReviewStatus = z.infer<typeof SprintReviewStatusSchema>;

export const SprintReviewerSchema = z.object({
  role: z.string(),
  label: z.string(),
  status: z.enum(["waiting", "reviewing", "approved", "changes_requested", "blocked"]),
  detail: z.string(),
});
export type SprintReviewer = z.infer<typeof SprintReviewerSchema>;

// PlanItem is recursive via `subtasks` — zod's `z.lazy` handles the cycle.
// Mirrors `src/minions/models/sprint_plan.py` (Phase B of openspec/
// enriched-sprint-planning).
export type PlanItem = {
  title: string;
  rationale: string;
  acceptance_criteria: string;
  estimated_effort: "xs" | "s" | "m" | "l" | "xl";
  suggested_owner_role: string | null;
  subtasks: PlanItem[];
};
export const PlanItemSchema: z.ZodType<PlanItem> = z.lazy(() =>
  z.object({
    title: z.string(),
    rationale: z.string().optional().default(""),
    acceptance_criteria: z.string().optional().default(""),
    estimated_effort: z.enum(["xs", "s", "m", "l", "xl"]).optional().default("m"),
    suggested_owner_role: z.string().nullable().optional().transform((v) => v ?? null),
    subtasks: z.array(PlanItemSchema).optional().default([]),
  }),
);

export const StructuredSprintPlanSchema = z.object({
  goal: z.string(),
  features: z.array(PlanItemSchema).optional().default([]),
  bugs: z.array(PlanItemSchema).optional().default([]),
  tech_debt: z.array(PlanItemSchema).optional().default([]),
  ops: z.array(PlanItemSchema).optional().default([]),
  docs: z.array(PlanItemSchema).optional().default([]),
  risks: z.array(z.string()).optional().default([]),
  // Meeting minutes from the multi-voice debate (Phase A). Empty list
  // for legacy / fallback paths.
  discussion: z.array(z.string()).optional().default([]),
});
export type StructuredSprintPlan = z.infer<typeof StructuredSprintPlanSchema>;

export const TaskSchema = z.object({
  id: z.string(),
  decision_id: z.string(),
  project: z.string(),
  sprint_number: z.number().nullable(),
  category: z.enum(["feature", "bug", "tech_debt", "ops", "docs"]),
  title: z.string(),
  description: z.string(),
  acceptance_criteria: z.string(),
  owner_role: z.string(),
  // owner_agent_id / owner_display_name may be null when the Task is
  // `unassigned` (Phase D — every eligible candidate at WIP cap; the
  // backlog sweep assigns later).
  owner_agent_id: z.string().nullable(),
  owner_display_name: z.string().nullable(),
  estimated_effort: z.enum(["xs", "s", "m", "l", "xl"]),
  status: z.enum([
    "unassigned",
    "queued",
    "in_progress",
    "review",
    "done",
    "blocked",
    "cancelled",
  ]),
  pr_url: z.string().nullable(),
  pr_number: z.number().nullable(),
  created_at: z.string().datetime(),
  updated_at: z.string().datetime(),
  completed_at: z.string().datetime().nullable(),
  // Catch-all populated by refinement (e.g. parent_plan_item for subtasks).
  payload: z.record(z.string(), z.unknown()).optional().default({}),
});
export type Task = z.infer<typeof TaskSchema>;

export const AgentMemorySchema = z.object({
  id: z.string(),
  agent_id: z.string(),
  sprint_number: z.number().nullable(),
  decision_id: z.string().nullable(),
  task_id: z.string().nullable(),
  pr_url: z.string().nullable(),
  event: z.string(),
  summary: z.string(),
  details: z.string().nullable(),
  created_at: z.string().datetime(),
  tier: z.enum(["hot", "cold"]),
});
export type AgentMemory = z.infer<typeof AgentMemorySchema>;

export const SprintCardSchema = z.object({
  decision_id: z.string(),
  project: z.string(),
  column: SprintColumnSchema,
  type: z.string(),
  risk: z.enum(["low", "medium", "high"]),
  sprint_number: z.number().nullable(),
  structured_plan: StructuredSprintPlanSchema.nullable(),
  tasks: z.array(TaskSchema),
  priority: z.enum(["p1", "p2", "p3"]),
  expedited: z.boolean(),
  requested_by_role: z.string().nullable(),
  summary: z.string(),
  proposer_role: z.string().nullable(),
  proposer_display_name: z.string().nullable(),
  avatar_seed: z.string(),
  pr_url: z.string().nullable(),
  pr_number: z.number().nullable(),
  ci_conclusion: z.string().nullable(),
  age_minutes: z.number(),
  stalled: z.boolean(),
  has_security_review: z.boolean(),
  has_devils_advocate: z.boolean(),
  // True iff this card is eligible for the auto-merge button.
  can_auto_merge: z.boolean(),
  review_status: SprintReviewStatusSchema,
  review_status_label: z.string(),
  crew_last_action: z.string(),
  reviewers: z.array(SprintReviewerSchema),
  iteration_count: z.number().int().nonnegative(),
  // Cached classification of the most recent owner-sweep retry trigger.
  // One of "ci_failure" | "merge_conflict" | "review_changes_requested",
  // or null when no retry has fired yet.
  last_failure_kind: z.string().nullable(),
  last_followup_at: z.string().datetime().nullable(),
  qa_review_posted_at: z.string().datetime().nullable(),
  operator_comment_posted: z.boolean(),
  merge_blocked_reason: z.string().nullable(),
  human_handoff_posted_at: z.string().datetime().nullable(),
  // Live crew indicator — populated when an `activity_log.crew_started` event
  // for this decision_id has no matching `crew_finished`/`crew_failed` within
  // the last 10 minutes. Tells the operator "this card is being worked on
  // right now" instead of letting it sit stale in the Approved column.
  live_crew: z
    .object({
      crew: z.string(),
      started_at: z.string().datetime(),
      agents: z.array(z.string()),
      run_id: z.string(),
    })
    .nullable(),
});
export type SprintCard = z.infer<typeof SprintCardSchema>;

export const SprintBoardSchema = z.object({
  projects: z.array(z.string()), // for the tab strip; sorted
  cards: z.array(SprintCardSchema),
});
export type SprintBoard = z.infer<typeof SprintBoardSchema>;

// ---------- Agile cadence ----------

export const AgileArtifactSchema = z.object({
  id: z.string(),
  project: z.string(),
  ritual: z.enum(["scrum", "sprint_planning", "monthly_planning", "monthly_demo"]),
  summary: z.string(),
  blockers: z.array(z.string()),
  next_actions: z.array(z.string()),
  related_pr_urls: z.array(z.string()),
  created_at: z.string().datetime(),
});
export type AgileArtifact = z.infer<typeof AgileArtifactSchema>;

export const PMAnswerSchema = z.object({
  id: z.string(),
  project: z.string(),
  question: z.string(),
  answer: z.string(),
  citations: z.array(z.string()),
  escalated_to: z.string().nullable(),
  created_at: z.string().datetime(),
});
export type PMAnswer = z.infer<typeof PMAnswerSchema>;

export const AgilePanelSchema = z.object({
  projects: z.array(z.string()),
  artifacts: z.array(AgileArtifactSchema),
  pm_answers: z.array(PMAnswerSchema),
});
export type AgilePanel = z.infer<typeof AgilePanelSchema>;

// ---------- Crew transcripts ----------

export const CrewTranscriptMessageSchema = z.object({
  id: z.string(),
  run_id: z.string(),
  project: z.string(),
  crew: z.string(),
  agent_role: z.string(),
  agent_display_name: z.string().nullable(),
  sequence: z.number().int().nonnegative(),
  role_in_conversation: z.enum([
    "pitch",
    "rebuttal",
    "synthesis",
    "review",
    "task_output",
    "other",
  ]),
  content: z.string(),
  created_at: z.string().datetime(),
});
export type CrewTranscriptMessage = z.infer<typeof CrewTranscriptMessageSchema>;

// ---------- Meeting room (living-org-spaces Surface A) ----------

export const MeetingStatusSchema = z.enum(["in_progress", "completed", "failed"]);
export type MeetingStatus = z.infer<typeof MeetingStatusSchema>;

export const SeatPositionSchema = z.enum([
  "north",
  "northeast",
  "east",
  "southeast",
  "south",
  "southwest",
  "west",
  "northwest",
  "center",
]);
export type SeatPosition = z.infer<typeof SeatPositionSchema>;

export const SeatSchema = z.object({
  agent_role: z.string(),
  agent_display_name: z.string().nullable(),
  seat_position: SeatPositionSchema,
  // True when this seat owns the most recent turn in the meeting (within the
  // live window). Used to render the pulsing-halo speaker indicator.
  is_speaking_now: z.boolean(),
  // Most recent thing THIS seat said. Drives the chat-bubble that floats
  // above each agent at the round-table. null when this seat hasn't taken
  // a turn yet in the meeting.
  last_turn_preview: z.string().nullable(),
  last_turn_sequence: z.number().int().nullable(),
});
export type Seat = z.infer<typeof SeatSchema>;

export const MeetingTurnSchema = z.object({
  sequence: z.number().int().nonnegative(),
  agent_role: z.string(),
  agent_display_name: z.string().nullable(),
  role_in_conversation: z.enum([
    "pitch",
    "rebuttal",
    "synthesis",
    "review",
    "task_output",
    "other",
  ]),
  content_preview: z.string(), // truncated to ~3 lines for the summary panel
  content_full: z.string(), // full text for the transcript drawer
  created_at: z.string(),
});
export type MeetingTurn = z.infer<typeof MeetingTurnSchema>;

/** One row on the /meetings list page — represents one crew run. */
export const MeetingSummarySchema = z.object({
  run_id: z.string(),
  crew: z.string(),
  ritual_label: z.string(), // operator-facing label from MEETING_RITUALS
  ritual_agenda: z.string(),
  multi_agent: z.boolean(), // true → round-table; false → solo focused-work card
  project: z.string().nullable(),
  decision_id: z.string().nullable(),
  started_at: z.string(),
  last_event_at: z.string(),
  status: MeetingStatusSchema,
  seats: z.array(SeatSchema),
  latest_turn: MeetingTurnSchema.nullable(),
  total_turns: z.number().int().nonnegative(),
});
export type MeetingSummary = z.infer<typeof MeetingSummarySchema>;

/** Full meeting detail returned by `/api/meetings/[run_id]`. */
export const MeetingDetailSchema = MeetingSummarySchema.extend({
  turns: z.array(MeetingTurnSchema),
});
export type MeetingDetail = z.infer<typeof MeetingDetailSchema>;

export const MeetingListSchema = z.object({
  meetings: z.array(MeetingSummarySchema),
});
export type MeetingList = z.infer<typeof MeetingListSchema>;

// ---------- Spokesperson interviews (removed) ----------
// The operator-facing Spokesperson console was retired; its schemas,
// API routes, and UI component were removed. The `spokesperson` *agent
// role* (PM answerer) still exists server-side and is unaffected.

// ---------- Headline counters ----------

export const HeadlineCountersSchema = z.object({
  open_prs: z.number(),
  pending_approvals: z.number(),
  agents_active_5min: z.number(),
  queued_fixes: z.number(),
});
export type HeadlineCounters = z.infer<typeof HeadlineCountersSchema>;

// ---------- Site Sentry ----------

export const SiteHealthCheckSchema = z.object({
  check_path: z.string(),
  ok: z.boolean(),
  status_code: z.number().nullable(),
  latency_ms: z.number().nullable(),
  error: z.string().nullable(),
  last_check_at: z.string(),
  last_ok_at: z.string().nullable(),
  last_failed_at: z.string().nullable(),
  p50_ms_24h: z.number(),
  p99_ms_24h: z.number(),
  uptime_24h: z.number(),
  samples_24h: z.number(),
});
export type SiteHealthCheck = z.infer<typeof SiteHealthCheckSchema>;

export const SiteHealthProjectSchema = z.object({
  project: z.string(),
  ok: z.boolean(),
  checks: z.array(SiteHealthCheckSchema),
  // TLS cert expiry for the project's host (from the latest probe), plus
  // days-until and severity computed server-side. null when no https probe.
  cert_expires_at: z.string().nullable(),
  cert_days_until: z.number().nullable(),
  cert_severity: z.enum(["ok", "amber", "red", "overdue"]).nullable(),
});
export type SiteHealthProject = z.infer<typeof SiteHealthProjectSchema>;

// Renewal radar: a declared license renewal or credential rotation, with
// severity computed against today. Dates only — never secret values.
export const RenewalSchema = z.object({
  project: z.string(),
  kind: z.enum(["license", "secret_rotation"]),
  name: z.string(),
  due: z.string(), // YYYY-MM-DD
  url: z.string().nullable(),
  note: z.string().nullable(),
  days_until: z.number(),
  severity: z.enum(["ok", "amber", "red", "overdue"]),
});
export type Renewal = z.infer<typeof RenewalSchema>;

export const SiteHealthSchema = z.object({
  projects: z.array(SiteHealthProjectSchema),
  renewals: z.array(RenewalSchema),
});
export type SiteHealth = z.infer<typeof SiteHealthSchema>;
