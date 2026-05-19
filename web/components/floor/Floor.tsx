"use client";

import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { type AgentMemory, type AgentState } from "@/lib/schemas";
import { agentSeedFor, iconFor, prettyRole } from "@/lib/roles";
import { colorFor, registerProjects } from "@/lib/project-color";
import { vitalityFromAge } from "@/lib/recency";
import { Avatar } from "@/components/Avatar";
import { formatDistanceToNowStrict } from "date-fns";

type AgentsResponse = { agents: AgentState[] };
type MemoryResponse = { memory: AgentMemory[] };

const TREE_LEVELS = [
  {
    key: "pod",
    label: "Project Pod",
    roles: ["product_owner", "manager", "tech_team_lead"],
  },
  {
    key: "borrowed",
    label: "Borrowed Specialists",
    roles: [
      "principal_engineer",
      "team_architect",
      "cloud_devops",
      "senior_devops",
      "devsecops",
      "security_champion",
      "qa_engineer",
    ],
  },
  {
    key: "delivery",
    label: "Assigned Delivery",
    roles: ["senior_engineer", "engineer", "intern", "data_engineer", "documentation_engineer"],
  },
  {
    key: "guardrails",
    label: "Audit + Guardrails",
    roles: [
      "chief_auditor",
      "process_auditor",
      "code_auditor",
      "cost_auditor",
      "devils_advocate",
      "test_architect",
    ],
  },
];

const EXECUTIVE_ROLES = new Set(["ceo", "cto", "managing_director", "org_owner"]);
const SPECIALIST_ROLES = new Set([
  "principal_engineer",
  "team_architect",
  "cloud_devops",
  "senior_devops",
  "devsecops",
  "security_champion",
  "qa_engineer",
]);
const DELIVERY_POOL_ROLES = new Set([
  "senior_engineer",
  "engineer",
  "intern",
  "data_engineer",
  "documentation_engineer",
]);
const AUDIT_ROLES = new Set([
  "chief_auditor",
  "process_auditor",
  "code_auditor",
  "cost_auditor",
  "devils_advocate",
  "test_architect",
  "performance_engineer",
]);

async function fetchAgents(): Promise<AgentsResponse> {
  const r = await fetch("/api/agents", { cache: "no-store" });
  if (!r.ok) throw new Error("agents fetch failed");
  return r.json();
}

async function fetchMemory(agentId: string, includeCold: boolean): Promise<MemoryResponse> {
  const params = new URLSearchParams();
  if (includeCold) params.set("include_cold", "true");
  const suffix = params.toString() ? `?${params.toString()}` : "";
  const r = await fetch(`/api/agent-memory/${encodeURIComponent(agentId)}${suffix}`, {
    cache: "no-store",
  });
  if (!r.ok) throw new Error("agent memory fetch failed");
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
  const [selected, setSelected] = useState<AgentState | null>(null);
  const [spotlightAgentId, setSpotlightAgentId] = useState<string | null>(null);
  const [selectedProject, setSelectedProject] = useState<string | null>(null);
  const [showAllProjects, setShowAllProjects] = useState(false);
  const [showShared, setShowShared] = useState(false);
  const { data } = useQuery({
    queryKey: ["agents", referenceNow ?? "live"],
    queryFn: fetchAgents,
    initialData: { agents: initial },
    refetchInterval: referenceNow ? false : 3_000,
  });

  const agents = data.agents;
  const refMs = referenceNow ? new Date(referenceNow).getTime() : null;

  useEffect(() => {
    function onSpotlight(event: Event) {
      const detail = (event as CustomEvent<{ agentId: string | null }>).detail;
      setSpotlightAgentId(detail?.agentId ?? null);
    }
    window.addEventListener("minions:spotlight", onSpotlight);
    return () => window.removeEventListener("minions:spotlight", onSpotlight);
  }, []);

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
  const projectGroups = byProject.filter(([project]) => project !== "Shared");
  const sharedGroup = byProject.find(([project]) => project === "Shared") ?? null;
  const sharedMembers = sharedGroup?.[1] ?? [];
  const activeProject = selectedProject ?? projectGroups[0]?.[0] ?? null;
  const visibleProjects = showAllProjects
    ? projectGroups
    : projectGroups.filter(([project]) => project === activeProject);
  const capacity = useMemo(() => buildCapacity(agents), [agents]);
  const recommendations = useMemo(
    () => buildAllocationRecommendations(agents, capacity),
    [agents, capacity],
  );

  if (agents.length === 0) {
    return (
      <div className="rounded-xl border border-[var(--line)] bg-[var(--bg-surface)] p-8 text-center text-[var(--text-muted)]">
        No agents recorded yet. They will appear here once any project sees its
        first activity event.
      </div>
    );
  }

  return (
    <>
      <div className="rounded-2xl border border-[var(--line)] bg-[var(--bg-elevated)] p-4 shadow-sm">
        <div className="mb-3 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <h2 className="text-base font-semibold tracking-tight">Shared company bench</h2>
            <p className="text-sm text-[var(--text-muted)]">
              Senior leaders and specialists span the portfolio. Project pods borrow capacity as work becomes active.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              className={`rounded-lg border px-3 py-1.5 text-xs font-medium transition ${
                showAllProjects
                  ? "border-[var(--accent)] bg-sky-50 text-[var(--accent)]"
                  : "border-[var(--line)] bg-white text-[var(--text-muted)] hover:border-[var(--accent)]"
              }`}
              onClick={() => setShowAllProjects((value) => !value)}
            >
              {showAllProjects ? "Focused project" : "All projects"}
            </button>
            {sharedGroup && (
              <button
                type="button"
                className={`rounded-lg border px-3 py-1.5 text-xs font-medium transition ${
                  showShared
                    ? "border-[var(--accent)] bg-sky-50 text-[var(--accent)]"
                    : "border-[var(--line)] bg-white text-[var(--text-muted)] hover:border-[var(--accent)]"
                }`}
                onClick={() => setShowShared((value) => !value)}
              >
                {showShared ? "Hide detailed bench" : "Detailed bench"}
              </button>
            )}
          </div>
        </div>
        <CapacitySummary capacity={capacity} />
        <AllocationRecommendations items={recommendations} />
        {sharedMembers.length > 0 && (
          <SharedBench members={sharedMembers} refMs={refMs} onSelect={setSelected} />
        )}
      </div>

      <div className="rounded-2xl border border-[var(--line)] bg-[var(--bg-elevated)] p-4 shadow-sm">
        <div className="mb-3 flex flex-col gap-1">
          <h2 className="text-base font-semibold tracking-tight">Project pods</h2>
          <p className="text-sm text-[var(--text-muted)]">
            Each pod stays small: owner, manager, lead, plus borrowed contributors when the work needs them.
          </p>
        </div>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
          {projectGroups.map(([project, members]) => (
            <ProjectTile
              key={project}
              project={project}
              members={members}
              selected={project === activeProject && !showAllProjects}
              onClick={() => {
                setSelectedProject(project);
                setShowAllProjects(false);
              }}
            />
          ))}
        </div>
      </div>

      <div className={`grid grid-cols-1 gap-4 ${showAllProjects ? "2xl:grid-cols-2" : ""}`}>
        {visibleProjects.map(([project, members]) => (
          <ProjectFloor
            key={project}
            project={project}
            members={members}
            refMs={refMs}
            onSelect={setSelected}
            spotlightAgentId={spotlightAgentId}
          />
        ))}
        {showShared && sharedGroup && (
          <ProjectFloor
            project="Shared Bench Detail"
            members={sharedGroup[1]}
            refMs={refMs}
            onSelect={setSelected}
            spotlightAgentId={spotlightAgentId}
          />
        )}
      </div>
      {selected && (
        <AgentInspector
          agent={selected}
          refMs={refMs}
          onClose={() => setSelected(null)}
        />
      )}
    </>
  );
}

function buildCapacity(agents: AgentState[]) {
  const total = {
    executive: 0,
    specialist: 0,
    delivery: 0,
    audit: 0,
  };
  const assigned = {
    executive: 0,
    specialist: 0,
    delivery: 0,
    audit: 0,
  };
  for (const agent of agents) {
    const bucket = bucketForRole(agent.role);
    total[bucket] += agent.seats;
    if (agent.project || agent.in_flight) {
      assigned[bucket] += Math.max(1, agent.project ? agent.seats : 0);
    }
  }
  return (Object.keys(total) as Array<keyof typeof total>).map((bucket) => ({
    bucket,
    total: total[bucket],
    assigned: Math.min(total[bucket], assigned[bucket]),
    available: Math.max(0, total[bucket] - assigned[bucket]),
  }));
}

function bucketForRole(role: string): "executive" | "specialist" | "delivery" | "audit" {
  if (EXECUTIVE_ROLES.has(role)) return "executive";
  if (SPECIALIST_ROLES.has(role)) return "specialist";
  if (DELIVERY_POOL_ROLES.has(role)) return "delivery";
  if (AUDIT_ROLES.has(role)) return "audit";
  return "delivery";
}

function CapacitySummary({
  capacity,
}: {
  capacity: Array<{ bucket: string; total: number; assigned: number; available: number }>;
}) {
  const labels: Record<string, string> = {
    executive: "Executive",
    specialist: "Specialists",
    delivery: "Engineering pool",
    audit: "Audit guardrails",
  };
  return (
    <div className="mb-4 grid grid-cols-1 gap-2 md:grid-cols-2 xl:grid-cols-4">
      {capacity.map((item) => (
        <div key={item.bucket} className="rounded-xl border border-[var(--line)] bg-white/70 p-3">
          <div className="text-[10px] font-semibold uppercase tracking-wider text-[var(--text-muted)]">
            {labels[item.bucket] ?? item.bucket}
          </div>
          <div className="mt-1 flex items-end justify-between gap-2">
            <div className="text-xl font-semibold text-[var(--text-primary)]">
              {item.assigned}/{item.total}
            </div>
            <div className="text-xs text-[var(--text-muted)]">
              {item.available} available
            </div>
          </div>
          <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-100">
            <div
              className="h-full rounded-full bg-[var(--accent)]"
              style={{ width: `${item.total > 0 ? (item.assigned / item.total) * 100 : 0}%` }}
            />
          </div>
        </div>
      ))}
    </div>
  );
}

function buildAllocationRecommendations(
  agents: AgentState[],
  capacity: Array<{ bucket: string; total: number; assigned: number; available: number }>,
): string[] {
  const delivery = capacity.find((item) => item.bucket === "delivery");
  const specialist = capacity.find((item) => item.bucket === "specialist");
  const projectActivity = new Map<string, number>();
  for (const agent of agents) {
    if (!agent.project || !agent.last_event_at) continue;
    const ts = new Date(agent.last_event_at).getTime();
    if (Number.isNaN(ts)) continue;
    projectActivity.set(agent.project, Math.max(projectActivity.get(agent.project) ?? 0, ts));
  }
  const activeProject = [...projectActivity.entries()].sort((a, b) => b[1] - a[1])[0]?.[0];
  const items: string[] = [];
  if (delivery && delivery.available > 0) {
    items.push(
      activeProject
        ? `${delivery.available} delivery seat${delivery.available === 1 ? "" : "s"} can be loaned into ${activeProject} or the next approved sprint item.`
        : `${delivery.available} delivery seat${delivery.available === 1 ? "" : "s"} are available for the next approved sprint item.`,
    );
  }
  if (specialist && specialist.available > 0) {
    items.push(
      `${specialist.available} senior specialist seat${specialist.available === 1 ? "" : "s"} can cover architecture, deployment, QA, or security investigations across projects.`,
    );
  }
  if (items.length === 0) {
    items.push("All visible capacity is currently assigned or monitoring active work.");
  }
  return items.slice(0, 2);
}

function AllocationRecommendations({ items }: { items: string[] }) {
  return (
    <div className="mb-4 rounded-xl border border-[var(--line)] bg-white/70 p-3">
      <div className="text-[10px] font-semibold uppercase tracking-wider text-[var(--text-muted)]">
        Allocation readout
      </div>
      <div className="mt-2 grid gap-1.5 md:grid-cols-2">
        {items.map((item) => (
          <div key={item} className="rounded-lg bg-[var(--bg-surface)] px-3 py-2 text-sm leading-5 text-[var(--text-primary)]">
            {item}
          </div>
        ))}
      </div>
    </div>
  );
}

function SharedBench({
  members,
  refMs,
  onSelect,
}: {
  members: AgentState[];
  refMs: number | null;
  onSelect: (agent: AgentState) => void;
}) {
  const visible = members
    .filter((agent) => !AUDIT_ROLES.has(agent.role))
    .sort((a, b) => sharedSort(a.role) - sharedSort(b.role));
  return (
    <div className="grid grid-cols-1 gap-2 lg:grid-cols-2 2xl:grid-cols-4">
      {visible.map((agent) => (
        <BenchCard key={agent.id} agent={agent} refMs={refMs} onSelect={onSelect} />
      ))}
    </div>
  );
}

function sharedSort(role: string): number {
  if (EXECUTIVE_ROLES.has(role)) return 0;
  if (SPECIALIST_ROLES.has(role)) return 1;
  if (DELIVERY_POOL_ROLES.has(role)) return 2;
  return 3;
}

function BenchCard({
  agent,
  refMs,
  onSelect,
}: {
  agent: AgentState;
  refMs: number | null;
  onSelect: (agent: AgentState) => void;
}) {
  const roleTierColor = `var(--color-role-${agent.role_tier})`;
  const liveNowMs = useCurrentTime(refMs === null);
  const ageMinutes = agent.last_event_at
    ? Math.max(0, ((refMs ?? liveNowMs) - new Date(agent.last_event_at).getTime()) / 60_000)
    : null;
  const vitality = vitalityFromAge(ageMinutes);
  const state = agent.in_flight ? "assigned now" : agent.last_event_at ? "monitoring" : "available";
  return (
    <button
      type="button"
      onClick={() => onSelect(agent)}
      className="rounded-xl border border-[var(--line)] bg-white/75 p-3 text-left transition hover:-translate-y-0.5 hover:border-[var(--accent)]/50 hover:shadow-sm"
      style={{ opacity: vitality.brightness, filter: vitality.filter }}
    >
      <div className="flex items-center gap-3">
        <Avatar
          seed={agentSeedFor(agent.role, agent.project)}
          size={40}
          ring={agent.in_flight ? roleTierColor : undefined}
          mood={agent.in_flight ? "active" : "idle"}
        />
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-[var(--text-primary)]">
            {prettyRole(agent.role)}
          </div>
          <div className="text-xs text-[var(--text-muted)]">
            {agent.seats > 1 ? `${agent.seats} seats · ` : ""}{state}
          </div>
        </div>
      </div>
      {agent.live_run ? (
        <div
          className="mt-2 truncate rounded border border-emerald-500/50 bg-emerald-500/15 px-2 py-1 text-xs text-emerald-900"
          title={agent.live_run.decision_summary ?? agent.live_run.crew}
        >
          <span className="inline-flex items-center gap-1.5">
            <span className="relative inline-flex h-1.5 w-1.5">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-500/70" />
              <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-emerald-500" />
            </span>
            working on:{" "}
            {agent.live_run.decision_summary ?? `${prettyRole(agent.live_run.crew)} run`}
          </span>
        </div>
      ) : (
        <div className="mt-2 truncate rounded bg-[var(--bg-surface)] px-2 py-1 text-xs text-[var(--text-muted)]">
          {agent.last_output ?? "Ready to be assigned across projects"}
        </div>
      )}
    </button>
  );
}

function ProjectTile({
  project,
  members,
  selected,
  onClick,
}: {
  project: string;
  members: AgentState[];
  selected: boolean;
  onClick: () => void;
}) {
  const color = colorFor(project === "Shared" ? null : project);
  const newest = members
    .map((agent) => (agent.last_event_at ? new Date(agent.last_event_at).getTime() : null))
    .filter((value): value is number => value !== null)
    .sort((a, b) => b - a)[0];
  const active = members.filter((agent) => agent.in_flight).length;
  const label = newest
    ? formatDistanceToNowStrict(new Date(newest), { addSuffix: true })
    : "no activity yet";

  return (
    <button
      type="button"
      className={`rounded-xl border px-4 py-3 text-left transition hover:-translate-y-0.5 hover:shadow-sm ${
        selected
          ? "border-[var(--accent)] bg-sky-50"
          : "border-[var(--line)] bg-white/75 hover:border-[var(--accent)]/50"
      }`}
      onClick={onClick}
    >
      <div className="flex items-center gap-2">
        <span className="size-2.5 rounded-full" style={{ backgroundColor: color }} />
        <span className="truncate text-base font-semibold">{project}</span>
      </div>
      <div className="mt-2 text-sm text-[var(--text-muted)]">
        {members.length} pod role{members.length === 1 ? "" : "s"} · {active > 0 ? `${active} assigned now` : "ready"}
      </div>
      <div className="mt-1 truncate text-xs text-[var(--text-muted)]">{label}</div>
    </button>
  );
}

function ProjectFloor({
  project,
  members,
  refMs,
  onSelect,
  spotlightAgentId,
}: {
  project: string;
  members: AgentState[];
  refMs: number | null;
  onSelect: (agent: AgentState) => void;
  spotlightAgentId: string | null;
}) {
  const color = colorFor(project === "Shared" ? null : project);
  const byLevel = useMemo(() => {
    const used = new Set<string>();
    const levels = TREE_LEVELS.map((level) => {
      const list = members
        .filter((agent) => level.roles.includes(agent.role))
        .sort((a, b) => level.roles.indexOf(a.role) - level.roles.indexOf(b.role));
      for (const agent of list) used.add(agent.id);
      return { ...level, members: list };
    });
    const overflow = members.filter((agent) => !used.has(agent.id));
    if (overflow.length > 0) {
      levels.push({
        key: "extended",
        label: "Extended Crew",
        roles: overflow.map((agent) => agent.role),
        members: overflow,
      });
    }
    return levels.filter((level) => level.members.length > 0);
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
    <section
      className="relative overflow-hidden rounded-2xl border border-[var(--line)] bg-[var(--bg-surface)] p-4 shadow-sm xl:p-5"
      style={{
        backgroundImage:
          "linear-gradient(var(--line) 1px, transparent 1px), linear-gradient(90deg, var(--line) 1px, transparent 1px), radial-gradient(circle at top left, color-mix(in srgb, var(--accent) 12%, transparent), transparent 36%)",
        backgroundSize: "32px 32px",
        backgroundPosition: "-1px -1px",
      }}
    >
      <header className="mb-4 flex flex-wrap items-baseline gap-2">
        <span
          className="inline-block size-2.5 rounded-full"
          style={{ backgroundColor: color }}
          aria-hidden
        />
        <h2 className="text-xl font-semibold tracking-tight">{project}</h2>
        <span className="ml-2 text-sm text-[var(--text-muted)]">
          {members.length} role{members.length === 1 ? "" : "s"} · last active {newestLabel}
        </span>
      </header>

      <div className="org-tree org-tree-horizontal">
        {byLevel.map((level, index) => (
          <TreeLevel
            key={level.key}
            label={level.label}
            agents={level.members}
            projectColor={color}
            refMs={refMs}
            isLast={index === byLevel.length - 1}
            onSelect={onSelect}
            spotlightAgentId={spotlightAgentId}
          />
        ))}
      </div>
    </section>
  );
}

function TreeLevel({
  label,
  agents,
  projectColor,
  refMs,
  isLast,
  onSelect,
  spotlightAgentId,
}: {
  label: string;
  agents: AgentState[];
  projectColor: string;
  refMs: number | null;
  isLast: boolean;
  onSelect: (agent: AgentState) => void;
  spotlightAgentId: string | null;
}) {
  return (
    <div className="tree-column relative flex min-w-0 flex-col items-center rounded-xl border border-[var(--line)] bg-white/65 px-3 py-3">
      <div className="mb-3 max-w-full rounded-full border border-[var(--line)] bg-white/90 px-3 py-1.5 text-center text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
        {label}
      </div>
      <div className="grid w-full grid-cols-1 gap-2">
        {agents.map((agent) => (
          <AgentCard
            key={agent.id}
            agent={agent}
            projectColor={projectColor}
            refMs={refMs}
            onSelect={onSelect}
            spotlighted={spotlightAgentId === agent.id}
          />
        ))}
      </div>
      {!isLast && (
        <span
          className="tree-connector pointer-events-none absolute top-1/2 -right-4 hidden h-px w-8"
          style={{
            background:
              "linear-gradient(to right, color-mix(in srgb, var(--accent) 60%, transparent), var(--line))",
          }}
          aria-hidden
        />
      )}
    </div>
  );
}

function AgentCard({
  agent,
  projectColor,
  refMs,
  onSelect,
  spotlighted,
}: {
  agent: AgentState;
  projectColor: string;
  refMs: number | null;
  onSelect: (agent: AgentState) => void;
  spotlighted: boolean;
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
  const mood = agent.in_flight
    ? "active"
    : vitality.level === "cold" || vitality.level === "stale"
      ? "leisure"
      : "idle";
  const stateLabel = agent.in_flight
    ? "working"
    : vitality.level === "cold" || vitality.level === "stale"
      ? "available"
      : "available";

  return (
    <button
      type="button"
      className={`group relative flex min-h-[142px] w-full flex-col gap-2 rounded-xl border border-[var(--line)] bg-[var(--bg-elevated)] px-3 py-3 text-left shadow-sm transition hover:-translate-y-0.5 hover:border-[var(--accent)]/50 hover:shadow-md focus:outline-none focus:ring-2 focus:ring-[var(--accent)]/40 ${vitality.level === "live" ? "" : "breathe"} ${spotlighted ? "agent-spotlight" : ""}`}
      style={cardStyle}
      title={agent.last_event ?? agent.role}
      onClick={() => onSelect(agent)}
    >
      <div className="flex flex-col items-center gap-2 text-center">
        <div className={`relative ${vitality.showPulse ? "pulse-halo" : ""}`}>
          <Avatar
            seed={agentSeedFor(agent.role, agent.project)}
            size={56}
            ring={vitality.showPulse ? roleTierColor : undefined}
            mood={mood}
          />
        </div>
        <div className="min-w-0">
          <div className="flex items-center justify-center gap-1.5">
            <span
              className="font-mono text-base leading-none"
              style={{ color: roleTierColor }}
              aria-hidden
            >
              {iconFor(agent.role_tier)}
            </span>
            <span className="truncate text-base font-semibold">{displayName}</span>
          </div>
          <div className="truncate text-xs text-[var(--text-muted)]">
            {prettyRole(agent.role)}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {agent.seats > 1 && (
            <span
              className="rounded px-1.5 text-[11px] font-medium uppercase tracking-wider text-[var(--text-muted)]"
              style={{ background: "var(--bg-surface)" }}
              title={`${agent.seats} seats configured`}
            >
              × {agent.seats}
            </span>
          )}
          <span
            className="rounded px-1.5 text-[11px] uppercase tracking-wider"
            style={{
              color: roleTierColor,
              background: `color-mix(in srgb, ${roleTierColor} 14%, transparent)`,
            }}
          >
            {stateLabel}
          </span>
        </div>
      </div>

      <div
        className="truncate text-xs text-[var(--text-muted)]"
        title={agent.last_event ?? undefined}
      >
        {agent.last_event_at
          ? `${agent.last_event ?? "idle"} · ${ageLabel}`
          : "never fired · configured only"}
      </div>

      {agent.live_run ? (
        <div
          className="truncate rounded border border-emerald-500/50 bg-emerald-500/15 px-2 py-1.5 text-xs text-emerald-900"
          title={agent.live_run.decision_summary ?? agent.live_run.crew}
        >
          <span className="inline-flex items-center gap-1.5">
            <span className="relative inline-flex h-1.5 w-1.5">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-500/70" />
              <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-emerald-500" />
            </span>
            <span className="font-medium">working on:</span>{" "}
            {agent.live_run.decision_summary ?? `${prettyRole(agent.live_run.crew)} run`}
          </span>
        </div>
      ) : (
        <div
          className="truncate rounded bg-[var(--bg-surface)] px-2 py-1.5 text-xs text-[var(--text-muted)]"
          title={agent.last_output ?? undefined}
        >
          {agent.last_output ? `last: ${agent.last_output}` : "last: waiting for first assignment"}
        </div>
      )}

      <div className="mt-auto text-center text-xs font-medium text-[var(--accent)] opacity-0 transition group-hover:opacity-100 group-focus:opacity-100">
        Open activity profile
      </div>

      {/* Project color accent strip */}
      <span
        className="pointer-events-none absolute inset-y-2 left-0 w-0.5 rounded-full"
        style={{ background: projectColor, opacity: 0.8 }}
      />
    </button>
  );
}

function AgentInspector({
  agent,
  refMs,
  onClose,
}: {
  agent: AgentState;
  refMs: number | null;
  onClose: () => void;
}) {
  const [includeCold, setIncludeCold] = useState(false);
  const memory = useQuery({
    queryKey: ["agent-memory", agent.id, includeCold],
    queryFn: () => fetchMemory(agent.id, includeCold),
    initialData: { memory: [] },
  });
  const liveNowMs = useCurrentTime(refMs === null);
  const eventMs = agent.last_event_at ? new Date(agent.last_event_at).getTime() : null;
  const ageMinutes =
    eventMs === null || isNaN(eventMs)
      ? null
      : Math.max(0, ((refMs ?? liveNowMs) - eventMs) / 60_000);
  const vitality = vitalityFromAge(ageMinutes);
  const roleTierColor = `var(--color-role-${agent.role_tier})`;
  const displayName = agent.display_name?.trim() || prettyRole(agent.role);
  const mood = agent.in_flight
    ? "active"
    : vitality.level === "cold" || vitality.level === "stale"
      ? "leisure"
      : "idle";
  const stateLabel = agent.in_flight
    ? "working now"
    : vitality.level === "cold" || vitality.level === "stale"
      ? "available for assignment"
      : "available";
  const lastSeen = agent.last_event_at
    ? formatDistanceToNowStrict(new Date(agent.last_event_at), {
        addSuffix: true,
        ...(refMs ? { now: new Date(refMs) } : {}),
      })
    : "not yet recorded";

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/30 px-4 py-6 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-label={`${displayName} activity profile`}
      onClick={onClose}
    >
      <section
        className="w-full max-w-2xl rounded-2xl border border-[var(--line)] bg-[var(--bg-elevated)] p-5 shadow-2xl"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="flex items-start justify-between gap-4">
          <div className="flex items-center gap-4">
            <Avatar
              seed={agentSeedFor(agent.role, agent.project)}
              size={72}
              ring={roleTierColor}
              mood={mood}
            />
            <div>
              <div className="text-lg font-semibold">{displayName}</div>
              <div className="text-sm text-[var(--text-muted)]">
                {prettyRole(agent.role)} {agent.project ? `@ ${agent.project}` : "@ Shared"}
              </div>
              <div className="mt-2 flex flex-wrap gap-2 text-[11px]">
                <span className="rounded-full bg-sky-100 px-2 py-1 font-medium text-sky-700">
                  {stateLabel}
                </span>
                <span className="rounded-full bg-slate-100 px-2 py-1 text-slate-600">
                  last seen {lastSeen}
                </span>
                {agent.seats > 1 && (
                  <span className="rounded-full bg-slate-100 px-2 py-1 text-slate-600">
                    {agent.seats} seats
                  </span>
                )}
              </div>
            </div>
          </div>
          <button
            type="button"
            className="rounded-full border border-[var(--line)] px-3 py-1 text-sm text-[var(--text-muted)] transition hover:border-[var(--accent)] hover:text-[var(--text-primary)]"
            onClick={onClose}
          >
            Close
          </button>
        </header>

        <div className="mt-5 grid gap-3 sm:grid-cols-3">
          <InspectorStat label="Current lane" value={agent.in_flight ? "Active run" : "Ready"} />
          <InspectorStat label="Today cost" value={`$${agent.cost_today_usd.toFixed(3)}`} />
          <InspectorStat label="Model tier" value={agent.tier === "unknown" ? "roster" : agent.tier} />
        </div>

        {agent.live_run && (
          <div className="mt-5 rounded-xl border border-emerald-500/50 bg-emerald-500/10 p-4">
            <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-emerald-800">
              <span className="relative inline-flex h-2 w-2">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-500/60" />
                <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-500" />
              </span>
              Currently working · started {liveElapsed(agent.live_run.started_at, refMs ?? liveNowMs)} ago
            </div>
            <p className="mt-2 text-sm leading-6 text-emerald-900">
              In a <span className="font-medium">{prettyRole(agent.live_run.crew)}</span>{" "}
              working session
              {agent.live_run.project ? ` for ${agent.live_run.project}` : ""}
              {agent.live_run.decision_summary ? (
                <>
                  {" "}on{" "}
                  <span className="font-medium">
                    &ldquo;{agent.live_run.decision_summary}&rdquo;
                  </span>
                </>
              ) : null}
              .
            </p>
            {agent.live_run.decision_id && (
              <div className="mt-2 text-xs text-emerald-800/80">
                Decision{" "}
                <span className="font-mono">{agent.live_run.decision_id.slice(0, 8)}</span>{" "}
                · run <span className="font-mono">{agent.live_run.run_id.slice(0, 8)}</span>
              </div>
            )}
          </div>
        )}

        <div className="mt-5 rounded-xl border border-[var(--line)] bg-[var(--bg-surface)] p-4">
          <div className="text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)]">
            Last output
          </div>
          <p className="mt-2 text-sm leading-6">
            {agent.last_output ??
              "No assignment has been recorded for this agent yet. They are configured in the crew and will light up when the next cron or approved decision reaches them."}
          </p>
        </div>

        <div className="mt-5 rounded-xl border border-[var(--line)] bg-[var(--bg-surface)] p-4">
          <div className="flex items-center justify-between gap-3">
            <div className="text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)]">
              Memory
            </div>
            <button
              type="button"
              onClick={() => setIncludeCold((value) => !value)}
              className="rounded border border-[var(--line)] px-2 py-1 text-xs text-[var(--text-muted)] hover:border-[var(--accent)] hover:text-[var(--text-primary)]"
            >
              {includeCold ? "Hot only" : "Load older sprints"}
            </button>
          </div>
          {memory.data.memory.length > 0 ? (
            <ol className="mt-3 space-y-2">
              {memory.data.memory.slice(0, 6).map((record) => (
                <li key={record.id} className="rounded-lg bg-white/70 px-3 py-2">
                  <div className="text-sm leading-5">{record.summary}</div>
                  <div className="mt-1 flex flex-wrap gap-2 text-[11px] text-[var(--text-muted)]">
                    <span>{record.tier}</span>
                    <span>{record.event}</span>
                    {record.sprint_number !== null && <span>Sprint {record.sprint_number}</span>}
                    {record.pr_url && (
                      <a className="text-[var(--accent)] hover:underline" href={record.pr_url} target="_blank" rel="noreferrer">
                        PR
                      </a>
                    )}
                  </div>
                </li>
              ))}
            </ol>
          ) : (
            <div className="mt-3 rounded-lg border border-dashed border-[var(--line)] bg-white/60 px-3 py-4 text-sm text-[var(--text-muted)]">
              No durable memory recorded for this agent yet.
            </div>
          )}
        </div>

        <div className="mt-5">
          <div className="mb-3 text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)]">
            Recent activity
          </div>
          {agent.recent_events.length > 0 ? (
            <ol className="space-y-2">
              {agent.recent_events.map((event) => (
                <li
                  key={`${event.ts}-${event.event}-${event.decision_id ?? ""}`}
                  className="flex gap-3 rounded-lg border border-[var(--line)] bg-white/70 px-3 py-2"
                >
                  <span
                    className="mt-1 size-2 shrink-0 rounded-full"
                    style={{ backgroundColor: roleTierColor }}
                    aria-hidden
                  />
                  <div className="min-w-0">
                    <div className="text-sm">{event.sentence}</div>
                    <div className="mt-0.5 flex flex-wrap gap-2 text-[11px] text-[var(--text-muted)]">
                      <span>{formatDistanceToNowStrict(new Date(event.ts), { addSuffix: true })}</span>
                      {event.pr_url && (
                        <a
                          className="font-medium text-[var(--accent)] hover:underline"
                          href={event.pr_url}
                          target="_blank"
                          rel="noreferrer"
                        >
                          PR
                        </a>
                      )}
                      {event.decision_id && <span>Decision {event.decision_id.slice(0, 8)}</span>}
                    </div>
                  </div>
                </li>
              ))}
            </ol>
          ) : (
            <div className="rounded-lg border border-dashed border-[var(--line)] bg-white/60 px-3 py-4 text-sm text-[var(--text-muted)]">
              No recent run history for this crew member yet. They stay available until the next scheduled crew touch or assignment.
            </div>
          )}
        </div>
      </section>
    </div>
  );
}

function InspectorStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-[var(--line)] bg-[var(--bg-surface)] px-3 py-3">
      <div className="text-[10px] font-semibold uppercase tracking-wider text-[var(--text-muted)]">
        {label}
      </div>
      <div className="mt-1 text-sm font-medium">{value}</div>
    </div>
  );
}

function liveElapsed(startedAtIso: string, now: number): string {
  const elapsedSec = Math.max(0, Math.round((now - new Date(startedAtIso).getTime()) / 1000));
  if (elapsedSec < 60) return `${elapsedSec}s`;
  const min = Math.floor(elapsedSec / 60);
  const sec = elapsedSec % 60;
  if (min < 60) return sec ? `${min}m ${sec}s` : `${min}m`;
  const hr = Math.floor(min / 60);
  const remMin = min % 60;
  return remMin ? `${hr}h ${remMin}m` : `${hr}h`;
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
