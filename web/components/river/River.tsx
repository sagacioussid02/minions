"use client";

import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { describe, deepLinks } from "@/lib/activity-renderer";
import { type ActivityEvent } from "@/lib/schemas";
import { prettyRole, tierFor } from "@/lib/roles";
import {
  type EventGroup,
  agentsForEvent,
  formatDuration,
  groupByRun,
  liveStartedEvents,
} from "@/lib/stage-grouping";
import { format } from "date-fns";

type EventsResponse = { events: ActivityEvent[] };

async function fetchEvents(): Promise<EventsResponse> {
  const r = await fetch("/api/events?limit=200", { cache: "no-store" });
  if (!r.ok) throw new Error("events fetch failed");
  return r.json();
}

export function River({ initial }: { initial: ActivityEvent[] }) {
  const { data } = useQuery({
    queryKey: ["events"],
    queryFn: fetchEvents,
    initialData: { events: initial },
  });

  const live = useMemo(() => liveStartedEvents(data.events), [data.events]);
  const groups = useMemo(() => groupByRun(data.events), [data.events]);

  return (
    <div className="rounded-xl border border-[var(--line)] bg-[var(--bg-surface)]">
      <div className="flex items-center justify-between border-b border-[var(--line)] px-4 py-2">
        <h2 className="text-base font-medium tracking-tight">Activity</h2>
        <span className="text-sm text-[var(--text-muted)]">
          {data.events.length} event{data.events.length === 1 ? "" : "s"} ·{" "}
          {groups.length} item{groups.length === 1 ? "" : "s"}
        </span>
      </div>

      {live.length > 0 && (
        <div className="border-b border-emerald-500/30 bg-emerald-500/10 px-4 py-2">
          <div className="mb-2 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wider text-emerald-800">
            <span className="relative inline-flex h-2 w-2">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-500/60" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-500" />
            </span>
            Now running · {live.length}
          </div>
          <ul className="space-y-1">
            {live.map((e) => (
              <LiveRow key={`live-${e.id}`} event={e} />
            ))}
          </ul>
        </div>
      )}

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

function useTick(ms: number): number {
  const [now, setNow] = useState(0);
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), ms);
    return () => window.clearInterval(id);
  }, [ms]);
  return now;
}

function LiveRow({ event }: { event: ActivityEvent }) {
  const now = useTick(15_000); // re-render every 15s so elapsed counter ticks
  const elapsedSec = Math.max(0, Math.round((now - new Date(event.ts).getTime()) / 1000));
  const elapsedLabel =
    elapsedSec < 60
      ? `${elapsedSec}s`
      : elapsedSec < 3600
        ? `${Math.floor(elapsedSec / 60)}m ${elapsedSec % 60}s`
        : `${Math.floor(elapsedSec / 3600)}h ${Math.floor((elapsedSec % 3600) / 60)}m`;
  const links = deepLinks(event);
  const crewLabel = event.crew ? prettyRole(event.crew) : "crew";
  const projectLabel = event.project ? ` @ ${event.project}` : "";
  const agents = agentsForEvent(event).map((agent) => prettyRole(agent));
  return (
    <li className="flex items-center gap-3 rounded-md bg-white/60 px-2 py-1.5 text-sm text-emerald-900">
      <span className="font-mono text-xs text-emerald-700">{elapsedLabel}</span>
      <span className="truncate">
        <span className="font-medium">{crewLabel}</span>
        {projectLabel}
        {agents.length > 0 && (
          <span className="text-emerald-700/80"> · {agents.join(", ")}</span>
        )}
      </span>
      <span className="ml-auto flex shrink-0 items-center gap-1.5">
        {links.map((l, i) =>
          l.href.startsWith("http") ? (
            <a
              key={i}
              href={l.href}
              target="_blank"
              rel="noreferrer"
              className="rounded border border-emerald-600/30 px-2 py-0.5 font-mono text-xs text-emerald-800 hover:border-emerald-600/60"
            >
              {l.label}
            </a>
          ) : (
            <a
              key={i}
              href={l.href}
              className="rounded border border-emerald-600/30 px-2 py-0.5 font-mono text-xs text-emerald-800 hover:border-emerald-600/60"
            >
              {l.label}
            </a>
          ),
        )}
      </span>
    </li>
  );
}

function ActivityRow({ event }: { event: ActivityEvent }) {
  const tier = event.role ? tierFor(event.role) : "engineering";
  const links = deepLinks(event);
  const ts = format(new Date(event.ts), "HH:mm:ss");
  return (
    <li className="row-in flex items-center gap-3 px-4 py-2 text-sm">
      <span className="w-20 shrink-0 font-mono text-xs text-[var(--text-muted)]">
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
              className="rounded border border-[var(--line)] px-2 py-0.5 font-mono text-xs text-[var(--text-muted)] hover:border-[var(--accent)]/40 hover:text-[var(--text-primary)]"
            >
              {l.label}
            </a>
          ) : (
            <a
              key={i}
              href={l.href}
              className="rounded border border-[var(--line)] px-2 py-0.5 font-mono text-xs text-[var(--text-muted)] hover:border-[var(--accent)]/40 hover:text-[var(--text-primary)]"
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
        className="flex w-full items-center gap-3 px-4 py-2 text-left text-sm hover:bg-[var(--bg-elevated)]/40"
      >
        <span className="w-20 shrink-0 font-mono text-xs text-[var(--text-muted)]">
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
          <span className="rounded bg-[var(--bg-elevated)] px-2 py-0.5 font-mono text-xs text-[var(--text-muted)]">
            {group.events.length} events
          </span>
          <span
            className="font-mono text-xs text-[var(--text-muted)] transition-transform"
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
