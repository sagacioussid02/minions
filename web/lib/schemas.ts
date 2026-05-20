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
  followup_attempts: z.number().int().nonnegative(),
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

// ---------- Spokesperson interviews ----------

export const InterviewCitationSchema = z.object({
  source_type: z.enum([
    "manifest",
    "readme",
    "docs",
    "decision",
    "pull_request",
    "agile_ritual",
    "activity",
    "cost",
    "role_memory",
    "code_scan",
    "consultation",
  ]),
  label: z.string(),
  reference: z.string().nullable(),
  excerpt: z.string(),
});
export type InterviewCitation = z.infer<typeof InterviewCitationSchema>;

export const InterviewThreadSchema = z.object({
  id: z.string(),
  scope: z.enum(["project", "organization"]),
  project: z.string().nullable(),
  spokesperson_role: z.string(),
  title: z.string(),
  created_at: z.string().datetime(),
  updated_at: z.string().datetime(),
});
export type InterviewThread = z.infer<typeof InterviewThreadSchema>;

export const ConsultationStatusSchema = z.enum([
  "queued",
  "gathering_memory",
  "scanning_code",
  "answered",
  "blocked",
]);
export type ConsultationStatus = z.infer<typeof ConsultationStatusSchema>;

export const ConfidenceSchema = z.enum(["high", "medium", "low", "unknown"]);
export type Confidence = z.infer<typeof ConfidenceSchema>;

export const ConsultationSchema = z.object({
  id: z.string(),
  thread_id: z.string(),
  message_id: z.string(),
  project: z.string().nullable(),
  consulted_role: z.string(),
  status: ConsultationStatusSchema,
  memory_summary: z.string().nullable(),
  code_scan_summary: z.string().nullable(),
  files_inspected: z.array(z.string()),
  note: z.string().nullable(),
  citations: z.array(InterviewCitationSchema),
  confidence: ConfidenceSchema,
  created_at: z.string().datetime(),
  updated_at: z.string().datetime(),
});
export type Consultation = z.infer<typeof ConsultationSchema>;

export const InterviewMessageSchema = z.object({
  id: z.string(),
  thread_id: z.string(),
  role: z.enum(["operator", "spokesperson", "consulted_agent"]),
  agent_role: z.string().nullable(),
  content: z.string(),
  citations: z.array(InterviewCitationSchema),
  consulted_roles: z.array(z.string()),
  confidence: ConfidenceSchema,
  follow_up_actions: z.array(z.string()),
  task_proposal_id: z.string().nullable(),
  created_at: z.string().datetime(),
});
export type InterviewMessage = z.infer<typeof InterviewMessageSchema>;

export const InterviewTaskProposalSchema = z.object({
  id: z.string(),
  thread_id: z.string(),
  message_id: z.string(),
  project: z.string().nullable(),
  owner_role: z.string(),
  title: z.string(),
  rationale: z.string(),
  status: z.enum(["pending", "converted", "dismissed"]),
  decision_id: z.string().nullable(),
  created_at: z.string().datetime(),
});
export type InterviewTaskProposal = z.infer<typeof InterviewTaskProposalSchema>;

export const InterviewBundleSchema = z.object({
  thread: InterviewThreadSchema,
  messages: z.array(InterviewMessageSchema),
  consultations: z.array(ConsultationSchema),
  tasks: z.array(InterviewTaskProposalSchema),
});
export type InterviewBundle = z.infer<typeof InterviewBundleSchema>;

export const SpokespersonRolesSchema = z.object({
  roles: z.array(z.string()),
});

export const SpokespersonProjectsSchema = z.object({
  projects: z.array(z.string()),
});

export const SpokespersonThreadsSchema = z.object({
  threads: z.array(InterviewThreadSchema),
});

export const SpokespersonAnswerSchema = z.object({
  thread: InterviewThreadSchema,
  operator_message: InterviewMessageSchema,
  answer_message: InterviewMessageSchema,
  consultations: z.array(ConsultationSchema),
  task: InterviewTaskProposalSchema.nullable(),
});
export type SpokespersonAnswer = z.infer<typeof SpokespersonAnswerSchema>;

// ---------- Headline counters ----------

export const HeadlineCountersSchema = z.object({
  open_prs: z.number(),
  pending_approvals: z.number(),
  agents_active_5min: z.number(),
  queued_fixes: z.number(),
});
export type HeadlineCounters = z.infer<typeof HeadlineCountersSchema>;
