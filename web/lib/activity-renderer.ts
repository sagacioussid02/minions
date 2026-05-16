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
  if (e.crew) return `${e.crew} crew`;
  return "system";
}

export function describe(e: ActivityEvent): string {
  const who = actor(e);
  switch (e.event) {
    case "crew_started":
      return `${who} started ${e.crew ?? "a"} crew`;
    case "crew_finished":
      return `${who} finished ${e.crew ?? "a"} crew`;
    case "decision_submitted":
      return `${who} proposed a Decision for review`;
    case "decision_resolved": {
      const status =
        (typeof e.payload?.["status"] === "string" && (e.payload["status"] as string)) ||
        "resolved";
      return `${who} → Decision ${status}`;
    }
    case "pr_opened":
      return `${who} opened a PR`;
    case "pr_merged":
      return `${who} merged a PR`;
    case "audit_finding_created":
      return `${who} raised an audit finding`;
    case "question_submitted":
      return `${who} raised a Question`;
    case "question_escalated":
      return `${who} escalated a Question to operator`;
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
  return out;
}
