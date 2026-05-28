"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { type AgentState } from "@/lib/schemas";
import { AgentChatPanel } from "@/components/agent-chat/AgentChatPanel";
import { AgentLabel } from "@/components/AgentLabel";

async function fetchAgents(): Promise<AgentState[]> {
  const r = await fetch("/api/agents", { cache: "no-store" });
  if (!r.ok) throw new Error("agents fetch failed");
  const body = await r.json();
  return body.agents as AgentState[];
}

const TIER_BADGE: Record<string, string> = {
  executive: "border-amber-400/40 text-amber-200",
  engineering: "border-cyan-400/40 text-cyan-200",
  audit: "border-fuchsia-400/40 text-fuchsia-200",
  specialist: "border-emerald-400/40 text-emerald-200",
};

export function RosterGrid({ initial }: { initial: AgentState[] }) {
  const { data } = useQuery({
    queryKey: ["roster"],
    queryFn: fetchAgents,
    initialData: initial,
    refetchInterval: 15_000,
  });
  const [search, setSearch] = useState("");
  const [tierFilter, setTierFilter] = useState<string>("all");
  const [projectFilter, setProjectFilter] = useState<string>("all");
  const [chatTarget, setChatTarget] = useState<AgentState | null>(null);

  const projects = useMemo(
    () =>
      Array.from(
        new Set(
          data
            .map((a) => a.project)
            .filter((p): p is string => Boolean(p))
        )
      ).sort(),
    [data]
  );

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return data
      .filter((a) => tierFilter === "all" || a.role_tier === tierFilter)
      .filter((a) =>
        projectFilter === "all"
          ? true
          : projectFilter === "(portfolio)"
            ? a.project === null
            : a.project === projectFilter
      )
      .filter((a) =>
        !q
          ? true
          : (a.display_name ?? "").toLowerCase().includes(q) ||
            a.role.toLowerCase().includes(q) ||
            (a.project ?? "").toLowerCase().includes(q)
      )
      .sort((a, b) => {
        // In-flight first, then alphabetical by display name.
        if (a.in_flight !== b.in_flight) return a.in_flight ? -1 : 1;
        const an = a.display_name ?? a.role;
        const bn = b.display_name ?? b.role;
        return an.localeCompare(bn);
      });
  }, [data, search, tierFilter, projectFilter]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3 text-xs">
        <input
          type="text"
          placeholder="search name / role / project…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="w-64 rounded border border-[var(--line)] bg-transparent px-2 py-1 text-xs outline-none focus:border-[var(--accent)]/60"
        />
        <select
          value={tierFilter}
          onChange={(e) => setTierFilter(e.target.value)}
          className="rounded border border-[var(--line)] bg-transparent px-2 py-1 text-xs outline-none focus:border-[var(--accent)]/60"
        >
          <option value="all">all tiers</option>
          <option value="executive">executive</option>
          <option value="engineering">engineering</option>
          <option value="audit">audit</option>
          <option value="specialist">specialist</option>
        </select>
        <select
          value={projectFilter}
          onChange={(e) => setProjectFilter(e.target.value)}
          className="rounded border border-[var(--line)] bg-transparent px-2 py-1 text-xs outline-none focus:border-[var(--accent)]/60"
        >
          <option value="all">all projects</option>
          <option value="(portfolio)">(portfolio)</option>
          {projects.map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </select>
        <span className="text-[var(--text-muted)]">
          {filtered.length} of {data.length}
        </span>
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {filtered.map((a) => (
          <Link
            key={a.id}
            href={`/roster/${encodeURIComponent(a.id)}`}
            className="group rounded-lg border border-[var(--line)] bg-[var(--surface-1)] p-3 transition hover:border-[var(--accent)]/40 hover:bg-[var(--surface-2)]"
          >
            <div className="mb-2 flex items-start justify-between gap-2">
              <AgentLabel displayName={a.display_name} role={a.role} />
              <span
                className={`shrink-0 rounded border px-1.5 py-0.5 text-[9px] uppercase tracking-wider ${TIER_BADGE[a.role_tier] ?? "border-[var(--line)] text-[var(--text-muted)]"}`}
              >
                {a.role_tier}
              </span>
            </div>
            <div className="mb-2 flex flex-wrap gap-1 text-[10px]">
              <span className="rounded bg-[var(--surface-2)] px-1.5 py-0.5 text-[var(--text-muted)]">
                {a.project ?? "portfolio"}
              </span>
              <span className="rounded bg-[var(--surface-2)] px-1.5 py-0.5 text-[var(--text-muted)]">
                {a.tier}
              </span>
              {a.seats > 1 && (
                <span className="rounded bg-[var(--surface-2)] px-1.5 py-0.5 text-[var(--text-muted)]">
                  {a.seats} seats
                </span>
              )}
            </div>
            <div className="flex items-center justify-between gap-2 text-[10px]">
              <div className="flex items-center gap-2">
                {a.in_flight ? (
                  <span className="flex items-center gap-1 text-emerald-300">
                    <span className="relative inline-flex size-2">
                      <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400/50" />
                      <span className="relative inline-flex size-2 rounded-full bg-emerald-400" />
                    </span>
                    in-flight
                  </span>
                ) : a.errored ? (
                  <span className="text-rose-300">errored</span>
                ) : (
                  <span className="text-[var(--text-muted)]">idle</span>
                )}
                {a.live_run && (
                  <span className="truncate text-[var(--text-muted)]">
                    · {a.live_run.crew}
                    {a.live_run.decision_summary
                      ? `: "${a.live_run.decision_summary.slice(0, 40)}"`
                      : ""}
                  </span>
                )}
              </div>
              <button
                type="button"
                onClick={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  setChatTarget(a);
                }}
                className="shrink-0 rounded border border-[var(--accent)]/40 bg-[var(--surface-2)] px-2 py-0.5 text-[10px] text-[var(--accent)] opacity-0 transition group-hover:opacity-100 hover:bg-[var(--accent)]/10 focus:opacity-100"
                aria-label={`Talk to ${a.display_name ?? a.role}`}
              >
                talk
              </button>
            </div>
          </Link>
        ))}
      </div>

      {chatTarget && (
        <AgentChatPanel agent={chatTarget} onClose={() => setChatTarget(null)} />
      )}

      {filtered.length === 0 && (
        <div className="rounded border border-dashed border-[var(--line)] p-6 text-center text-xs text-[var(--text-muted)]">
          No agents match these filters.
        </div>
      )}
    </div>
  );
}
