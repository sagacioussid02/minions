import { type ActivityEvent } from "@/lib/schemas";

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
    case "scrum_created":
      return "shared a daily scrum update";
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
