"use client";

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { type AgentState } from "@/lib/schemas";
import { iconFor, prettyRole, type RoleTier } from "@/lib/roles";
import { colorFor, registerProjects } from "@/lib/project-color";
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

export function Floor({ initial }: { initial: AgentState[] }) {
  const { data } = useQuery({
    queryKey: ["agents"],
    queryFn: fetchAgents,
    initialData: { agents: initial },
  });

  const agents = data.agents;

  // Group by project. Shared (null project) is a synthetic "Shared" floor.
  const byProject = useMemo(() => {
    const groups = new Map<string, AgentState[]>();
    for (const a of agents) {
      const key = a.project ?? "Shared";
      const arr = groups.get(key) ?? [];
      arr.push(a);
      groups.set(key, arr);
    }
    // Register palette assignments in a stable alphabetical order.
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
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 xl:grid-cols-3">
      {byProject.map(([project, members]) => (
        <ProjectFloor key={project} project={project} members={members} />
      ))}
    </div>
  );
}

function ProjectFloor({ project, members }: { project: string; members: AgentState[] }) {
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

  return (
    <div className="rounded-xl border border-[var(--line)] bg-[var(--bg-surface)] p-4">
      <div className="mb-3 flex items-center gap-2">
        <span
          className="inline-block size-2 rounded-full"
          style={{ backgroundColor: color }}
          aria-hidden
        />
        <span className="text-sm font-medium tracking-tight">{project}</span>
        <span className="ml-auto text-xs text-[var(--text-muted)]">
          {members.length} agent{members.length === 1 ? "" : "s"}
        </span>
      </div>

      <div className="flex flex-col gap-3">
        {ROW_ORDER.map((tier) => {
          const list = byTier.get(tier) ?? [];
          if (list.length === 0) return null;
          return (
            <div key={tier}>
              <div className="mb-1.5 text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
                {ROW_LABEL[tier]}
              </div>
              <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-3">
                {list.map((a) => (
                  <AgentCard key={a.id} agent={a} projectColor={color} />
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function AgentCard({ agent, projectColor }: { agent: AgentState; projectColor: string }) {
  const inFlight = agent.in_flight;
  const errored = agent.errored;

  const ageLabel = agent.last_event_at
    ? formatDistanceToNowStrict(new Date(agent.last_event_at), { addSuffix: true })
    : "—";

  const roleTierColor = `var(--color-role-${agent.role_tier})`;
  const ring = errored
    ? "ring-1 ring-[var(--state-danger)]"
    : inFlight
      ? "ring-1"
      : "";

  return (
    <div
      className={`group relative rounded-lg border border-[var(--line)] bg-[var(--bg-elevated)] px-2.5 py-2 transition-shadow hover:border-[var(--accent)]/40 ${ring} ${inFlight ? "" : "breathe"}`}
      style={inFlight ? ({ ["--tw-ring-color" as never]: roleTierColor } as React.CSSProperties) : undefined}
      title={agent.last_event ?? agent.role}
    >
      <div className="flex items-center gap-1.5">
        <span
          className="font-mono text-base leading-none"
          style={{ color: roleTierColor }}
          aria-hidden
        >
          {iconFor(agent.role_tier)}
        </span>
        <span className="truncate text-xs font-medium">{prettyRole(agent.role)}</span>
      </div>
      <div
        className="mt-1 truncate text-[10px] text-[var(--text-muted)]"
        title={agent.last_event ?? undefined}
      >
        {agent.last_event ? agent.last_event : "idle"} · {ageLabel}
      </div>

      {/* Project color accent strip on the left edge */}
      <span
        className="pointer-events-none absolute inset-y-1 left-0 w-0.5 rounded-full opacity-70"
        style={{ background: projectColor }}
      />
    </div>
  );
}
