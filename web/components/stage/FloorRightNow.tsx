"use client";

import { useEffect, useMemo, useState } from "react";
import { Avatar } from "@/components/Avatar";
import { agentsForEvent, formatDuration } from "@/lib/stage-grouping";
import { agentSeedFor, prettyRole, tierFor } from "@/lib/roles";
import { type ActivityEvent, type AgentState } from "@/lib/schemas";
import { decisionPhrase } from "@/lib/stage-sentences";

type VisibleHuddle = {
  key: string;
  event: ActivityEvent;
  leaving: boolean;
};

export function FloorRightNow({
  live,
  agents,
}: {
  live: ActivityEvent[];
  agents: AgentState[];
}) {
  const now = useTick(15_000);
  const huddles = useLeavingHuddles(live);
  const visible = huddles.slice(0, 6);
  const hidden = Math.max(0, huddles.length - visible.length);
  const rows = useMemo(() => tierRows(visible), [visible]);

  if (visible.length === 0) return null;

  return (
    <section className="rounded-lg border border-[var(--line)] bg-[var(--bg-surface)] p-4">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold uppercase tracking-wider text-[var(--text-muted)]">
          Floor right now
        </h2>
        <span className="text-xs text-[var(--text-muted)]">
          {live.length} live conversation{live.length === 1 ? "" : "s"}
        </span>
      </div>
      <div className="space-y-3">
        {rows.map((row) => (
          <div
            key={row.id}
            className="rounded-lg border border-[var(--line)] bg-[var(--bg-elevated)]/35 p-3"
          >
            <div className="mb-2 flex items-center gap-2">
              <span
                className="size-2 rounded-full"
                style={{ backgroundColor: `var(--color-role-${row.tone})` }}
              />
              <span className="text-[10px] font-semibold uppercase tracking-wider text-[var(--text-muted)]">
                {row.label}
              </span>
            </div>
            {row.huddles.length > 0 ? (
              <div className="grid grid-cols-1 gap-3 lg:grid-cols-2 2xl:grid-cols-3">
                {row.huddles.map((huddle) => (
                  <Huddle
                    key={huddle.key}
                    event={huddle.event}
                    agents={agents}
                    now={now}
                    leaving={huddle.leaving}
                  />
                ))}
              </div>
            ) : (
              <div className="rounded-md border border-dashed border-[var(--line)] px-3 py-4 text-center text-xs text-[var(--text-muted)]">
                No one on this row right now.
              </div>
            )}
          </div>
        ))}
        {hidden > 0 && (
          <div className="flex items-center justify-center rounded-lg border border-dashed border-[var(--line)] bg-[var(--bg-elevated)]/40 p-4 text-sm text-[var(--text-muted)]">
            + {hidden} more in the transcript
          </div>
        )}
      </div>
    </section>
  );
}

function Huddle({
  event,
  agents,
  now,
  leaving,
}: {
  event: ActivityEvent;
  agents: AgentState[];
  now: number;
  leaving: boolean;
}) {
  const roles = agentsForEvent(event);
  const elapsed = now > 0 ? formatDuration(now - new Date(event.ts).getTime()) : "now";
  const summary = decisionPhrase(event);
  const primaryRole = roles[0] ?? event.role ?? event.crew ?? "crew";
  const primaryTier = tierFor(primaryRole);

  return (
    <div
      className={`stage-huddle relative rounded-lg border border-[var(--line)] bg-[var(--bg-elevated)]/75 p-4 ${
        leaving ? "stage-huddle-leaving" : ""
      }`}
    >
      <div
        className={`stage-bubble mb-3 rounded-lg border px-3 py-2 text-sm leading-5 shadow-sm ${
          leaving ? "stage-bubble-leaving" : ""
        }`}
        style={{
          borderColor: `color-mix(in srgb, var(--color-role-${primaryTier}) 35%, var(--line))`,
          background: "rgb(255 255 255 / 0.72)",
        }}
      >
        <div className="mb-1 flex items-center gap-2 text-xs text-[var(--text-muted)]">
          <span className="relative flex size-2">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-[var(--accent)]/50" />
            <span className="relative inline-flex size-2 rounded-full bg-[var(--accent)]" />
          </span>
          {event.project ?? "company"} · {elapsed}
        </div>
        Working on &quot;{summary}&quot;
      </div>
      <div className="flex flex-wrap gap-3">
        {roles.length > 0 ? (
          roles.slice(0, 5).map((role) => (
            <AgentFace key={role} role={role} project={event.project} agents={agents} />
          ))
        ) : (
          <AgentFace role={primaryRole} project={event.project} agents={agents} />
        )}
      </div>
    </div>
  );
}

function useLeavingHuddles(live: ActivityEvent[]): VisibleHuddle[] {
  const [huddles, setHuddles] = useState<VisibleHuddle[]>(() =>
    live.map((event) => ({ key: huddleKey(event), event, leaving: false })),
  );

  useEffect(() => {
    const liveKeys = new Set(live.map(huddleKey));
    const timer = window.setTimeout(() => {
      setHuddles((current) => {
        const next = live.map((event) => ({ key: huddleKey(event), event, leaving: false }));

        for (const item of current) {
          if (!liveKeys.has(item.key) && !item.leaving) {
            next.push({ ...item, leaving: true });
          } else if (!liveKeys.has(item.key) && item.leaving) {
            next.push(item);
          }
        }

        return next;
      });
    }, 0);
    return () => window.clearTimeout(timer);
  }, [live]);

  const leavingKeys = huddles
    .filter((item) => item.leaving)
    .map((item) => item.key)
    .join("|");

  useEffect(() => {
    if (!leavingKeys) return;
    const keys = new Set(leavingKeys.split("|"));
    const timer = window.setTimeout(() => {
      setHuddles((items) => items.filter((item) => !keys.has(item.key)));
    }, 650);
    return () => {
      window.clearTimeout(timer);
    };
  }, [leavingKeys]);

  return huddles;
}

function huddleKey(event: ActivityEvent): string {
  return event.run_id ?? String(event.id);
}

function tierRows(huddles: VisibleHuddle[]) {
  const rows = [
    { id: "executive", label: "Executives", tone: "executive", huddles: [] as VisibleHuddle[] },
    { id: "engineering", label: "Engineering", tone: "engineering", huddles: [] as VisibleHuddle[] },
    { id: "audit", label: "Audit + specialists", tone: "audit", huddles: [] as VisibleHuddle[] },
  ];

  for (const huddle of huddles) {
    const role = agentsForEvent(huddle.event)[0] ?? huddle.event.role ?? huddle.event.crew ?? "crew";
    const tier = tierFor(role);
    if (tier === "executive") rows[0].huddles.push(huddle);
    else if (tier === "engineering") rows[1].huddles.push(huddle);
    else rows[2].huddles.push(huddle);
  }

  return rows;
}

function AgentFace({
  role,
  project,
  agents,
}: {
  role: string;
  project: string | null;
  agents: AgentState[];
}) {
  const tier = tierFor(role);
  const known = agents.find((agent) => agent.role === role && agent.project === project);
  return (
    <div className="flex items-center gap-2 rounded-full border border-[var(--line)] bg-white/70 px-2 py-1">
      <span className="pulse-halo relative inline-flex">
        <Avatar seed={agentSeedFor(role, project)} size={30} ring={`var(--color-role-${tier})`} />
      </span>
      <span className="max-w-36 truncate text-xs font-medium text-[var(--text-primary)]">
        {known?.display_name ?? prettyRole(role)}
      </span>
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
