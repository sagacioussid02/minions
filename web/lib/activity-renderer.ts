/**
 * Maps raw `activity_log` rows to natural-language sentences.
 *
 * The Python side emits a small known set of `event` strings — `crew_started`,
 * `crew_finished`, `decision_submitted`, `decision_resolved`, `pr_opened`,
 * `pr_merged`, `audit_finding_created`, plus the ad-hoc ones. Sprint 1 maps
 * the common cases and falls back gracefully for unknown events.
 */

import { type ActivityEvent } from "./schemas";
import { prettyRole } from "./roles";

function actor(e: ActivityEvent): string {
  const role = e.role ? prettyRole(e.role) : null;
  if (role && e.project) return `${role} @ ${e.project}`;
  if (role) return role;
  if (e.crew && e.project) return `${prettyRole(e.crew)} @ ${e.project}`;
  if (e.crew) return prettyRole(e.crew);
  return "system";
}

export function describe(e: ActivityEvent): string {
  const who = actor(e);
  switch (e.event) {
    case "crew_started":
      // If the actor was already derived from the crew name, do not repeat it.
      return e.role
        ? `${who} joined a ${prettyRole(e.crew ?? "")} working session`
        : `${who} working session started`;
    case "crew_finished":
      return e.role
        ? `${who} wrapped a ${prettyRole(e.crew ?? "")} working session`
        : `${who} working session wrapped`;
    case "crew_checkin":
      return `${who} checked in and is available`;
    case "decision_submitted":
      return `${who} proposed work for operator review`;
    case "decision_resolved": {
      const status =
        (typeof e.payload?.["status"] === "string" && (e.payload["status"] as string)) ||
        "resolved";
      return `${who} marked a Decision ${status}`;
    }
    case "pr_opened":
      return `${who} opened a PR`;
    case "pr_merged":
      return `${who} merged a PR`;
    case "audit_finding_created":
      return `${who} raised an audit finding`;
    case "question_submitted":
      return `${who} asked for operator input`;
    case "question_escalated":
      return `${who} escalated a blocker to the operator`;
    case "scrum_created":
      return `${who} published scrum notes`;
    case "sprint_planned":
      return `${who} prepared the sprint plan`;
    case "monthly_demo_ready":
      return `${who} prepared demo material`;
    case "pm_answered":
      return `${who} answered as Product Manager`;
    case "spokesperson_answered":
      return `${who} answered in the Leadership Room`;
    case "consultation_answered":
      return `${who} weighed in on a leadership question`;
    default:
      // Friendly fall-through: "engineer @ AaaG · pr_pushed_4_files"
      return `${who} · ${e.event}`;
  }
}

export function deepLinks(e: ActivityEvent): Array<{ label: string; href: string }> {
  const out: Array<{ label: string; href: string }> = [];
  if (e.decision_id) {
    out.push({ label: e.decision_id.slice(0, 8), href: `/decision/${e.decision_id}` });
  }
  const prUrl = (e.payload?.["pr_url"] as string | undefined) ?? null;
  if (prUrl) {
    const m = /\/pull\/(\d+)/.exec(prUrl);
    out.push({ label: m ? `#${m[1]}` : "PR", href: prUrl });
  }
  // crew-transcripts: agent_spoke rows link to the full per-run conversation,
  // anchored to this message's position in the thread.
  if (e.event === "agent_spoke" && e.run_id) {
    const seqRaw = e.payload?.["sequence"];
    const seq = typeof seqRaw === "number" ? seqRaw : undefined;
    out.push({
      label: "transcript",
      href: seq !== undefined
        ? `/stage/transcripts/${encodeURIComponent(e.run_id)}#msg-${seq}`
        : `/stage/transcripts/${encodeURIComponent(e.run_id)}`,
    });
  }
  return out;
}
