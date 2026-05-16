"use client";

import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { type AgentState } from "@/lib/schemas";
import { iconFor, prettyRole, type RoleTier } from "@/lib/roles";
import { colorFor, registerProjects } from "@/lib/project-color";
import { vitalityFromAge } from "@/lib/recency";
import { Avatar } from "@/components/Avatar";
import { formatDistanceToNowStrict } from "date-fns";

type AgentsResponse = { agents: AgentState[] };

const ROW_ORDER: RoleTier[] = ["executive", "engineering", "audit", "specialist"];
const ROW_LABEL: Record<RoleTier, string> = {
  executive: "Executive",
  engineering: "Engineering",
  audit: "Audit + Security",
  specialist: "Specialists",
};

async function fetchAgents(): Promise<AgentsResponse> {
  const r = await fetch("/api/agents", { cache: "no-store" });
  if (!r.ok) throw new Error("agents fetch failed");
  return r.json();
}

/**
 * `referenceNow` — when set, the Floor renders relative to this point in
 * time instead of `Date.now()`. Used by /replay so agent vitality reflects
 * the state at the scrubbed moment, not now.
 *
 * When `referenceNow` is set, polling is disabled (replay snapshots are
 * static — re-fetch only when the URL changes, handled at page level).
 */
export function Floor({
  initial,
  referenceNow,
}: {
  initial: AgentState[];
  referenceNow?: string;
}) {
  const { data } = useQuery({
    queryKey: ["agents", referenceNow ?? "live"],
    queryFn: fetchAgents,
    initialData: { agents: initial },
    refetchInterval: referenceNow ? false : 3_000,
  });

  const agents = data.agents;
  const refMs = referenceNow ? new Date(referenceNow).getTime() : null;

  const byProject = useMemo(() => {
    const groups = new Map<string, AgentState[]>();
    for (const a of agents) {
      const key = a.project ?? "Shared";
      const arr = groups.get(key) ?? [];
      arr.push(a);
      groups.set(key, arr);
    }
    registerProjects([...groups.keys()].filter((k) => k !== "Shared"));
    return [...groups.entries()].sort(([a], [b]) => {
      if (a === "Shared") return 1;
      if (b === "Shared") return -1;
      return a.localeCompare(b);
    });
  }, [agents]);

  if (agents.length === 0) {
    return (
      <div className="rounded-xl border border-[var(--line)] bg-[var(--bg-surface)] p-8 text-center text-[var(--text-muted)]">
        No agents recorded yet. They will appear here once any project sees its
        first activity event.
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
      {byProject.map(([project, members]) => (
        <ProjectFloor
          key={project}
          project={project}
          members={members}
          refMs={refMs}
        />
      ))}
    </div>
  );
}

function ProjectFloor({
  project,
  members,
  refMs,
}: {
  project: string;
  members: AgentState[];
  refMs: number | null;
}) {
  const color = colorFor(project === "Shared" ? null : project);
  const byTier = useMemo(() => {
    const m = new Map<RoleTier, AgentState[]>();
    for (const a of members) {
      const arr = m.get(a.role_tier) ?? [];
      arr.push(a);
      m.set(a.role_tier, arr);
    }
    return m;
  }, [members]);

  // Project-level summary line: "AaaG · 7 agents · last active 3h ago"
  const newest = useMemo(() => {
    const times = members
      .map((m) => (m.last_event_at ? new Date(m.last_event_at).getTime() : null))
      .filter((t): t is number => t !== null);
    return times.length ? Math.max(...times) : null;
  }, [members]);
  const newestLabel = newest
    ? formatDistanceToNowStrict(new Date(newest), { addSuffix: true })
    : "no recorded activity";

  return (
    <div
      className="relative overflow-hidden rounded-2xl border border-[var(--line)] bg-[var(--bg-surface)] p-5"
      style={{
        // Subtle "graph paper" floor lines beneath the cards.
        backgroundImage:
          "linear-gradient(var(--line) 1px, transparent 1px), linear-gradient(90deg, var(--line) 1px, transparent 1px)",
        backgroundSize: "32px 32px",
        backgroundPosition: "-1px -1px",
      }}
    >
      <header className="mb-4 flex items-baseline gap-2">
        <span
          className="inline-block size-2.5 rounded-full"
          style={{ backgroundColor: color }}
          aria-hidden
        />
        <h2 className="text-base font-semibold tracking-tight">{project}</h2>
        <span className="ml-2 text-xs text-[var(--text-muted)]">
          {members.length} agent{members.length === 1 ? "" : "s"} · last active {newestLabel}
        </span>
      </header>

      <div className="flex flex-col gap-4">
        {ROW_ORDER.map((tier) => {
          const list = byTier.get(tier) ?? [];
          if (list.length === 0) return null;
          return (
            <div key={tier} className="flex gap-3">
              <div className="w-16 shrink-0 pt-2 text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
                {ROW_LABEL[tier]}
              </div>
              <div className="grid flex-1 grid-cols-1 gap-2 sm:grid-cols-2">
                {list.map((a) => (
                  <AgentCard
                    key={a.id}
                    agent={a}
                    projectColor={color}
                    refMs={refMs}
                  />
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function AgentCard({
  agent,
  projectColor,
  refMs,
}: {
  agent: AgentState;
  projectColor: string;
  refMs: number | null;
}) {
  const errored = agent.errored;
  const liveNowMs = useCurrentTime(refMs === null);

  // When refMs is set (replay mode), compute age relative to it; otherwise
  // use the live clock from recency.ts.
  const ageMinutes = useMemo(() => {
    if (!agent.last_event_at) return null;
    const eventMs = new Date(agent.last_event_at).getTime();
    if (isNaN(eventMs)) return null;
    const refClock = refMs ?? liveNowMs;
    return Math.max(0, (refClock - eventMs) / 60_000);
  }, [agent.last_event_at, liveNowMs, refMs]);

  const vitality = vitalityFromAge(ageMinutes);

  const ageLabel = agent.last_event_at
    ? formatDistanceToNowStrict(new Date(agent.last_event_at), {
        addSuffix: true,
        ...(refMs ? { now: new Date(refMs) } : {}),
      })
    : "—";

  const roleTierColor = `var(--color-role-${agent.role_tier})`;

  const cardStyle: React.CSSProperties = {
    filter: errored ? undefined : vitality.filter,
    opacity: errored ? 1 : vitality.brightness,
    borderColor: errored ? "var(--state-danger)" : undefined,
    transition: "filter 600ms ease, opacity 600ms ease",
  };

  const fallbackName = prettyRole(agent.role).split(" ")[0]; // "Engineer", "Manager"
  const displayName = agent.display_name?.trim() || fallbackName;

  return (
    <div
      className={`group relative flex min-h-[88px] flex-col gap-2 rounded-xl border border-[var(--line)] bg-[var(--bg-elevated)] px-3 py-3 transition-shadow hover:border-[var(--accent)]/40 ${vitality.level === "live" ? "" : "breathe"}`}
      style={cardStyle}
      title={agent.last_event ?? agent.role}
    >
      <div className="flex items-center gap-3">
        <div className={`relative ${vitality.showPulse ? "pulse-halo" : ""}`}>
          <Avatar
            seed={agent.id}
            size={40}
            ring={vitality.showPulse ? roleTierColor : undefined}
          />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span
              className="font-mono text-sm leading-none"
              style={{ color: roleTierColor }}
              aria-hidden
            >
              {iconFor(agent.role_tier)}
            </span>
            <span className="truncate text-sm font-medium">{displayName}</span>
          </div>
          <div className="truncate text-[11px] text-[var(--text-muted)]">
            {prettyRole(agent.role)}
          </div>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-0.5">
          {agent.seats > 1 && (
            <span
              className="rounded px-1.5 text-[9px] font-medium uppercase tracking-wider text-[var(--text-muted)]"
              style={{ background: "var(--bg-surface)" }}
              title={`${agent.seats} seats configured`}
            >
              × {agent.seats}
            </span>
          )}
          <span
            className="rounded px-1.5 text-[9px] uppercase tracking-wider"
            style={{
              color: roleTierColor,
              background: `color-mix(in srgb, ${roleTierColor} 14%, transparent)`,
            }}
          >
            {vitality.level}
          </span>
        </div>
      </div>

      <div
        className="truncate text-[11px] text-[var(--text-muted)]"
        title={agent.last_event ?? undefined}
      >
        {agent.last_event_at
          ? `${agent.last_event ?? "idle"} · ${ageLabel}`
          : "never fired · configured only"}
      </div>

      {/* Project color accent strip */}
      <span
        className="pointer-events-none absolute inset-y-2 left-0 w-0.5 rounded-full"
        style={{ background: projectColor, opacity: 0.8 }}
      />
    </div>
  );
}

function useCurrentTime(enabled: boolean): number {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!enabled) return;
    const id = window.setInterval(() => setNow(Date.now()), 60_000);
    return () => window.clearInterval(id);
  }, [enabled]);

  return now;
}
