import { type ActivityEvent } from "@/lib/schemas";

function truncate(text: string, max: number): string {
  if (text.length <= max) return text;
  return text.slice(0, max - 1).trimEnd() + "…";
}

export function sentenceOnly(event: ActivityEvent, decisionSummary?: string | null): string {
  const summary = decisionSummary ?? event.decision_summary;
  switch (event.event) {
    case "crew_started":
      return summary ? `started work on "${summary}"` : "started a working session";
    case "crew_finished":
      return summary ? `wrapped work on "${summary}"` : "wrapped a working session";
    case "crew_failed":
      return event.error
        ? `hit a blocker: ${event.error}`
        : summary
          ? `hit a blocker while working on "${summary}"`
          : "hit a blocker";
    case "pr_opened":
      return summary ? `opened a PR for "${summary}"` : "opened a PR";
    case "decision_submitted":
      return summary ? `proposed "${summary}"` : "proposed work";
    case "decision_resolved":
      return summary ? `marked "${summary}" ready to move` : "marked a decision";
    case "consultation_answered":
      return event.payload?.["summary"]?.toString() || "weighed in on a leadership question";
    case "pm_answered":
      return summary ? `answered a product question about "${summary}"` : "answered a product question";
    case "spokesperson_answered":
      return "answered in the Leadership Room";
    case "sprint_planned":
      return summary ? `planned sprint work around "${summary}"` : "planned sprint work";
    case "agent_spoke": {
      const p = event.payload ?? {};
      const name = String(
        p["agent_display_name"] ?? p["agent_role"] ?? "an agent",
      );
      const phase = String(p["role_in_conversation"] ?? "task_output");
      const preview = String(p["preview"] ?? "");
      const verbs: Record<string, string> = {
        pitch: "proposed",
        rebuttal: "pushed back",
        synthesis: "summarized",
        review: "reviewed",
        task_output: "contributed",
        other: "said",
      };
      const verb = verbs[phase] ?? "said";
      const tail = preview ? `: "${truncate(preview, 200)}"` : "";
      return `${name} ${verb}${tail}`;
    }
    case "scrum_created": {
      const p = event.payload ?? {};
      const blockerCount =
        typeof p["blocker_count"] === "number" ? p["blocker_count"] : 0;
      const blockers = Array.isArray(p["blockers_preview"])
        ? (p["blockers_preview"] as unknown[]).map(String)
        : [];
      const nextActions = Array.isArray(p["next_actions_preview"])
        ? (p["next_actions_preview"] as unknown[]).map(String)
        : [];
      const head =
        blockerCount > 0
          ? `held scrum — ${blockerCount} blocker${blockerCount === 1 ? "" : "s"}`
          : "held scrum — no blockers";
      const tail = blockers[0]
        ? `: "${truncate(blockers[0], 120)}"`
        : "";
      const next = nextActions[0]
        ? `. Next: ${truncate(nextActions[0], 120)}`
        : "";
      return `${head}${tail}${next}`;
    }
    case "monthly_demo_ready":
      return "prepared a monthly demo";
    case "crew_checkin":
      return "checked in and is available";
    case "crew_heartbeat":
      return "checked the crew heartbeat";
    default:
      return event.event.replaceAll("_", " ");
  }
}

export function decisionPhrase(event: ActivityEvent): string {
  return event.decision_summary ?? event.payload?.["summary"]?.toString() ?? "current work";
}
