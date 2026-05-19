"use client";

import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChatStream } from "@/components/stage/ChatStream";
import { FloorRightNow } from "@/components/stage/FloorRightNow";
import { liveStartedEvents, projectListForEvents } from "@/lib/stage-grouping";
import { type ActivityEvent, type AgentState } from "@/lib/schemas";

type EventsResponse = { events: ActivityEvent[] };

async function fetchEvents(windowMinutes: number): Promise<EventsResponse> {
  const response = await fetch(`/api/events?window_minutes=${windowMinutes}&limit=160`, {
    cache: "no-store",
  });
  if (!response.ok) throw new Error("stage events fetch failed");
  return response.json();
}

export function Stage({
  initialEvents,
  agents,
}: {
  initialEvents: ActivityEvent[];
  agents: AgentState[];
}) {
  const [project, setProject] = useState<string | null>(null);
  const [windowMinutes, setWindowMinutes] = useState(60);
  useEffect(() => {
    window.localStorage.setItem("minions-stage-opened", "true");
    window.dispatchEvent(new Event("minions-stage-opened"));
  }, []);
  const { data } = useQuery({
    queryKey: ["stage-events", windowMinutes],
    queryFn: () => fetchEvents(windowMinutes),
    initialData: { events: initialEvents },
    refetchInterval: 15_000,
  });
  const events = data.events;
  const projects = useMemo(() => projectListForEvents(events), [events]);
  const filteredEvents = useMemo(
    () => (project ? events.filter((event) => event.project === project) : events),
    [events, project],
  );
  const live = useMemo(() => liveStartedEvents(filteredEvents), [filteredEvents]);

  return (
    <div className="mx-auto flex w-full max-w-[1180px] flex-col gap-4 p-4 xl:p-6">
      <section className="rounded-lg border border-[var(--line)] bg-[var(--bg-surface)] px-5 py-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-xl font-semibold tracking-tight text-[var(--text-primary)]">
              Agent stage
            </h1>
            <p className="mt-1 text-sm text-[var(--text-muted)]">
              Who is talking, what they are working on, and what just happened.
            </p>
          </div>
          <div className="flex rounded-lg border border-[var(--line)] bg-[var(--bg-elevated)] p-1">
            {[60, 180, 360].map((minutes) => (
              <button
                key={minutes}
                type="button"
                onClick={() => setWindowMinutes(minutes)}
                className={`rounded-md px-3 py-1 text-xs font-medium ${
                  windowMinutes === minutes
                    ? "bg-white text-[var(--text-primary)] shadow-sm"
                    : "text-[var(--text-muted)] hover:text-[var(--text-primary)]"
                }`}
              >
                {minutes === 60 ? "1h" : `${minutes / 60}h`}
              </button>
            ))}
          </div>
        </div>
        <div className="mt-4 flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => setProject(null)}
            className={filterClass(project === null)}
          >
            All
          </button>
          {projects.map((item) => (
            <button
              key={item}
              type="button"
              onClick={() => setProject(item)}
              className={filterClass(project === item)}
            >
              {item}
            </button>
          ))}
        </div>
      </section>

      <FloorRightNow live={live} agents={agents} />
      <ChatStream events={events} project={project} windowMinutes={windowMinutes} />
    </div>
  );
}

function filterClass(active: boolean): string {
  return `rounded-full border px-3 py-1 text-xs font-medium transition ${
    active
      ? "border-[var(--accent)] bg-[var(--accent)] text-white"
      : "border-[var(--line)] bg-[var(--bg-elevated)] text-[var(--text-muted)] hover:border-[var(--accent)]/40 hover:text-[var(--text-primary)]"
  }`;
}
