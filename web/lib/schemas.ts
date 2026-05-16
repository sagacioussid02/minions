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
  last_decision_id: z.string().nullable(),
  in_flight: z.boolean(),
  errored: z.boolean(),
  cost_today_usd: z.number(),
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

export const SprintCardSchema = z.object({
  decision_id: z.string(),
  project: z.string(),
  column: SprintColumnSchema,
  type: z.string(),
  risk: z.enum(["low", "medium", "high"]),
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
});
export type SprintCard = z.infer<typeof SprintCardSchema>;

export const SprintBoardSchema = z.object({
  projects: z.array(z.string()), // for the tab strip; sorted
  cards: z.array(SprintCardSchema),
});
export type SprintBoard = z.infer<typeof SprintBoardSchema>;

// ---------- Headline counters ----------

export const HeadlineCountersSchema = z.object({
  open_prs: z.number(),
  pending_approvals: z.number(),
  agents_active_5min: z.number(),
  queued_fixes: z.number(),
});
export type HeadlineCounters = z.infer<typeof HeadlineCountersSchema>;
