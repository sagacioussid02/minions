import { type ActivityEvent } from "@/lib/schemas";

export type EventGroup =
  | { kind: "single"; event: ActivityEvent }
  | {
      kind: "run";
      run_id: string;
      crew: string | null;
      project: string | null;
      role: string | null;
      events: ActivityEvent[];
      startTs: string;
      endTs: string;
      durationMs: number;
      live: boolean;
      failed: boolean;
    };

export function groupByRun(events: ActivityEvent[], nowMs = Date.now()): EventGroup[] {
  const liveIds = new Set(liveStartedEvents(events, nowMs).map((event) => event.run_id));
  const groups: EventGroup[] = [];
  let i = 0;
  while (i < events.length) {
    const event = events[i];
    if (!event.run_id) {
      groups.push({ kind: "single", event });
      i += 1;
      continue;
    }

    let j = i;
    while (j < events.length && events[j].run_id === event.run_id) j += 1;
    const block = events.slice(i, j);
    if (block.length === 1 && !liveIds.has(event.run_id)) {
      groups.push({ kind: "single", event: block[0] });
    } else {
      const startTs = block[block.length - 1].ts;
      const endTs = block[0].ts;
      groups.push({
        kind: "run",
        run_id: event.run_id,
        crew: event.crew,
        project: event.project,
        role: event.role,
        events: block,
        startTs,
        endTs,
        durationMs: new Date(endTs).getTime() - new Date(startTs).getTime(),
        live: liveIds.has(event.run_id),
        failed: block.some((item) => item.event === "crew_failed" || item.error),
      });
    }
    i = j;
  }
  return groups;
}

export function liveStartedEvents(events: ActivityEvent[], nowMs = Date.now()): ActivityEvent[] {
  const closedRunIds = new Set<string>();
  for (const event of events) {
    if (event.run_id && (event.event === "crew_finished" || event.event === "crew_failed")) {
      closedRunIds.add(event.run_id);
    }
  }
  const cutoffMs = nowMs - 10 * 60 * 1000;
  return events.filter(
    (event) =>
      event.event === "crew_started" &&
      event.run_id !== null &&
      !closedRunIds.has(event.run_id) &&
      new Date(event.ts).getTime() >= cutoffMs,
  );
}

export function formatDuration(ms: number): string {
  if (ms < 1000) return "instant";
  const seconds = Math.round(ms / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.round(minutes / 60);
  return `${hours}h`;
}

export function agentsForEvent(event: ActivityEvent): string[] {
  return Array.isArray(event.payload?.["agents"])
    ? (event.payload["agents"] as unknown[]).map((agent) => String(agent))
    : event.role
      ? [event.role]
      : [];
}

export function projectListForEvents(events: ActivityEvent[]): string[] {
  return Array.from(
    new Set(events.map((event) => event.project).filter((project): project is string => Boolean(project))),
  ).sort((a, b) => a.localeCompare(b));
}
