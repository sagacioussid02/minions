"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { describe, deepLinks } from "@/lib/activity-renderer";
import { type ActivityEvent } from "@/lib/schemas";
import { prettyRole, tierFor } from "@/lib/roles";
import { format } from "date-fns";

type EventsResponse = { events: ActivityEvent[] };

async function fetchEvents(): Promise<EventsResponse> {
  const r = await fetch("/api/events?limit=200", { cache: "no-store" });
  if (!r.ok) throw new Error("events fetch failed");
  return r.json();
}

/** A consecutive set of events sharing the same `run_id`. */
type EventGroup =
  | { kind: "single"; event: ActivityEvent }
  | {
      kind: "run";
      run_id: string;
      crew: string | null;
      project: string | null;
      role: string | null;
      events: ActivityEvent[]; // newest → oldest
      startTs: string;
      endTs: string;
      durationMs: number;
    };

/**
 * Fold the events list into groups. A "run" is a consecutive sequence of
 * events that share the same non-empty `run_id`. The Python crew lifecycle
 * (crew_started → ... → crew_finished) lives inside a single run_id, so
 * this collapses a chatty 5-row block into one feed card.
 *
 * Single events without a run_id (PR opens, audit findings, etc.) stay
 * standalone.
 */
function groupByRun(events: ActivityEvent[]): EventGroup[] {
  const groups: EventGroup[] = [];
  let i = 0;
  while (i < events.length) {
    const e = events[i];
    if (!e.run_id) {
      groups.push({ kind: "single", event: e });
      i += 1;
      continue;
    }
    // Walk forward over the contiguous block with the same run_id.
    let j = i;
    while (j < events.length && events[j].run_id === e.run_id) j += 1;
    const block = events.slice(i, j); // newest → oldest
    if (block.length === 1) {
      groups.push({ kind: "single", event: block[0] });
    } else {
      const startTs = block[block.length - 1].ts;
      const endTs = block[0].ts;
      groups.push({
        kind: "run",
        run_id: e.run_id,
        crew: e.crew,
        project: e.project,
        role: e.role,
        events: block,
        startTs,
        endTs,
        durationMs:
          new Date(endTs).getTime() - new Date(startTs).getTime(),
      });
    }
    i = j;
  }
  return groups;
}

function formatDuration(ms: number): string {
  if (ms < 1000) return "instant";
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.round(m / 60);
  return `${h}h`;
}

export function River({ initial }: { initial: ActivityEvent[] }) {
  const { data } = useQuery({
    queryKey: ["events"],
    queryFn: fetchEvents,
    initialData: { events: initial },
  });

  const groups = useMemo(() => groupByRun(data.events), [data.events]);

  return (
    <div className="rounded-xl border border-[var(--line)] bg-[var(--bg-surface)]">
      <div className="flex items-center justify-between border-b border-[var(--line)] px-4 py-2">
        <h2 className="text-sm font-medium tracking-tight">Activity</h2>
        <span className="text-xs text-[var(--text-muted)]">
          {data.events.length} event{data.events.length === 1 ? "" : "s"} ·{" "}
          {groups.length} item{groups.length === 1 ? "" : "s"}
        </span>
      </div>
      <ul className="max-h-[42vh] divide-y divide-[var(--line)] overflow-y-auto">
        {groups.length === 0 && (
          <li className="px-4 py-6 text-center text-sm text-[var(--text-muted)]">
            Quiet floor.
          </li>
        )}
        {groups.map((g, i) =>
          g.kind === "single" ? (
            <ActivityRow key={`s-${g.event.id}`} event={g.event} />
          ) : (
            <RunRow key={`r-${g.run_id}-${i}`} group={g} />
          ),
        )}
      </ul>
    </div>
  );
}

function ActivityRow({ event }: { event: ActivityEvent }) {
  const tier = event.role ? tierFor(event.role) : "engineering";
  const links = deepLinks(event);
  const ts = format(new Date(event.ts), "HH:mm:ss");
  return (
    <li className="row-in flex items-center gap-3 px-4 py-1.5 text-xs">
      <span className="w-16 shrink-0 font-mono text-[10px] text-[var(--text-muted)]">
        {ts}
      </span>
      <span
        className="size-1.5 shrink-0 rounded-full"
        style={{ backgroundColor: `var(--color-role-${tier})` }}
        aria-hidden
      />
      <span className="truncate text-[var(--text-primary)]">
        {describe(event)}
      </span>
      <span className="ml-auto flex shrink-0 items-center gap-1.5">
        {links.map((l, i) =>
          l.href.startsWith("http") ? (
            <a
              key={i}
              href={l.href}
              target="_blank"
              rel="noreferrer"
              className="rounded border border-[var(--line)] px-1.5 py-0.5 font-mono text-[10px] text-[var(--text-muted)] hover:border-[var(--accent)]/40 hover:text-[var(--text-primary)]"
            >
              {l.label}
            </a>
          ) : (
            <a
              key={i}
              href={l.href}
              className="rounded border border-[var(--line)] px-1.5 py-0.5 font-mono text-[10px] text-[var(--text-muted)] hover:border-[var(--accent)]/40 hover:text-[var(--text-primary)]"
            >
              {l.label}
            </a>
          ),
        )}
      </span>
    </li>
  );
}

function RunRow({ group }: { group: Extract<EventGroup, { kind: "run" }> }) {
  const [open, setOpen] = useState(false);
  const tier = group.role ? tierFor(group.role) : "engineering";
  const ts = format(new Date(group.endTs), "HH:mm:ss");
  const crewLabel = group.crew ? prettyRole(group.crew) : "crew";
  const projectLabel = group.project ? ` @ ${group.project}` : "";
  const duration = formatDuration(group.durationMs);

  return (
    <li className="row-in">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-3 px-4 py-1.5 text-left text-xs hover:bg-[var(--bg-elevated)]/40"
      >
        <span className="w-16 shrink-0 font-mono text-[10px] text-[var(--text-muted)]">
          {ts}
        </span>
        <span
          className="size-1.5 shrink-0 rounded-full"
          style={{ backgroundColor: `var(--color-role-${tier})` }}
          aria-hidden
        />
        <span className="truncate text-[var(--text-primary)]">
          <span className="font-medium">{crewLabel}</span>
          {projectLabel} — worked for {duration}
        </span>
        <span className="ml-auto flex shrink-0 items-center gap-1.5">
          <span className="rounded bg-[var(--bg-elevated)] px-1.5 py-0.5 font-mono text-[10px] text-[var(--text-muted)]">
            {group.events.length} events
          </span>
          <span
            className="font-mono text-[10px] text-[var(--text-muted)] transition-transform"
            style={{ transform: open ? "rotate(90deg)" : "none" }}
          >
            ▸
          </span>
        </span>
      </button>
      {open && (
        <ul className="border-t border-[var(--line)] bg-[var(--bg-elevated)]/30">
          {group.events.map((e) => (
            <ActivityRow key={`nest-${e.id}`} event={e} />
          ))}
        </ul>
      )}
    </li>
  );
}
