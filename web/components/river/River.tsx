"use client";

import { useQuery } from "@tanstack/react-query";
import { describe, deepLinks } from "@/lib/activity-renderer";
import { type ActivityEvent } from "@/lib/schemas";
import { tierFor } from "@/lib/roles";
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

  const events = data.events;

  return (
    <div className="rounded-xl border border-[var(--line)] bg-[var(--bg-surface)]">
      <div className="flex items-center justify-between border-b border-[var(--line)] px-4 py-2">
        <h2 className="text-sm font-medium tracking-tight">Activity</h2>
        <span className="text-xs text-[var(--text-muted)]">
          {events.length} recent event{events.length === 1 ? "" : "s"}
        </span>
      </div>
      <ul className="max-h-[40vh] divide-y divide-[var(--line)] overflow-y-auto">
        {events.length === 0 && (
          <li className="px-4 py-6 text-center text-sm text-[var(--text-muted)]">
            Quiet floor.
          </li>
        )}
        {events.map((e) => (
          <ActivityRow key={e.id} event={e} />
        ))}
      </ul>
    </div>
  );
}

function ActivityRow({ event }: { event: ActivityEvent }) {
  const tier = event.role ? tierFor(event.role) : "engineering";
  const links = deepLinks(event);
  const ts = format(new Date(event.ts), "HH:mm:ss");
  return (
    <li className="flex items-center gap-3 px-4 py-1.5 text-xs">
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
      <span className="ml-auto flex items-center gap-1.5">
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
