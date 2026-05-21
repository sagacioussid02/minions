"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { formatDistanceToNowStrict } from "date-fns";
import type { ActivityEvent, AgentState, AgilePanel } from "@/lib/schemas";
import { describe } from "@/lib/activity-renderer";
import { prettyRole } from "@/lib/roles";

type WindowKey = "today" | "week" | "sprint";

const WINDOWS: Array<{ key: WindowKey; label: string }> = [
  { key: "today", label: "Today" },
  { key: "week", label: "This week" },
  { key: "sprint", label: "Sprint" },
];

async function fetchAgile(): Promise<AgilePanel> {
  const r = await fetch("/api/agile", { cache: "no-store" });
  if (!r.ok) throw new Error("rhythm fetch failed");
  return r.json();
}

export function CompanyRhythm({
  initialAgile,
  agents,
  events,
}: {
  initialAgile: AgilePanel;
  agents: AgentState[];
  events: ActivityEvent[];
}) {
  const [window, setWindow] = useState<WindowKey>("today");
  const { data } = useQuery({
    queryKey: ["company-rhythm"],
    queryFn: fetchAgile,
    initialData: initialAgile,
    refetchInterval: 10_000,
  });

  const filteredEvents = useMemo(
    () => filterEvents(events, window).slice(0, 7),
    [events, window],
  );
  const artifacts = useMemo(
    () => filterArtifacts(data.artifacts, window).slice(0, 5),
    [data.artifacts, window],
  );
  const statuses = useMemo(() => buildAgentStatuses(agents).slice(0, 6), [agents]);
  const learnings = useMemo(
    () => buildLearnings(agents, filteredEvents, artifacts).slice(0, 4),
    [agents, filteredEvents, artifacts],
  );
  const observations = useMemo(
    () => buildObservations(agents, filteredEvents, artifacts).slice(0, 3),
    [agents, filteredEvents, artifacts],
  );

  return (
    <section className="rounded-2xl border border-[var(--line)] bg-[var(--bg-surface)] p-4 shadow-sm">
      <div className="mb-4 flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-wider text-[var(--accent)]">
            Company rhythm
          </p>
          <h2 className="mt-1 text-lg font-semibold tracking-tight text-[var(--text-primary)]">
            What the AI company is doing, noticing, and learning.
          </h2>
          <p className="mt-1 max-w-3xl text-sm leading-5 text-[var(--text-muted)]">
            Scrums, planning notes, active status, and proactive observations translated from the operating record.
          </p>
        </div>
        <div className="flex shrink-0 gap-1 rounded-xl border border-[var(--line)] bg-[var(--bg-elevated)] p-1">
          {WINDOWS.map((item) => (
            <button
              key={item.key}
              type="button"
              onClick={() => setWindow(item.key)}
              className={`rounded-lg px-3 py-1.5 text-xs transition ${
                window === item.key
                  ? "bg-white text-[var(--text-primary)] shadow-sm"
                  : "text-[var(--text-muted)] hover:text-[var(--text-primary)]"
              }`}
            >
              {item.label}
            </button>
          ))}
        </div>
      </div>

      <div className="grid gap-3 xl:grid-cols-[1.15fr_0.85fr]">
        <div className="grid gap-3">
          <RhythmPanel title="Operating feed">
            {filteredEvents.length === 0 ? (
              <Empty text="No operating events in this window yet." />
            ) : (
              filteredEvents.map((event) => (
                <div key={event.id} className="rounded-lg border border-[var(--line)] bg-[var(--bg-elevated)] p-3">
                  <div className="flex items-start justify-between gap-3">
                    <p className="text-sm leading-5 text-[var(--text-primary)]">{describe(event)}</p>
                    <span className="shrink-0 text-[10px] text-[var(--text-muted)]">
                      {formatDistanceToNowStrict(new Date(event.ts), { addSuffix: true })}
                    </span>
                  </div>
                  {event.project && (
                    <div className="mt-1 text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
                      {event.project}
                    </div>
                  )}
                </div>
              ))
            )}
          </RhythmPanel>

          <RhythmPanel title="Rituals">
            {artifacts.length === 0 ? (
              <Empty text="No scrum or planning artifact in this window." />
            ) : (
              artifacts.map((artifact) => (
                <article key={artifact.id} className="rounded-lg border border-[var(--line)] bg-[var(--bg-elevated)] p-3">
                  <div className="mb-1 flex flex-wrap items-center gap-2 text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
                    <span>{artifact.project}</span>
                    <span>·</span>
                    <span>{artifact.ritual.replaceAll("_", " ")}</span>
                    {artifact.blockers.length > 0 && (
                      <span className="rounded bg-[var(--state-warn)]/15 px-1.5 text-[var(--state-warn)]">
                        {artifact.blockers.length} blocker{artifact.blockers.length === 1 ? "" : "s"}
                      </span>
                    )}
                  </div>
                  <p className="text-sm leading-5 text-[var(--text-primary)]">{artifact.summary}</p>
                  {artifact.next_actions[0] && (
                    <p className="mt-2 text-xs leading-5 text-[var(--text-muted)]">
                      Next: {artifact.next_actions[0]}
                    </p>
                  )}
                </article>
              ))
            )}
          </RhythmPanel>
        </div>

        <div className="grid content-start gap-3">
          <RhythmPanel title="Agent status">
            <div className="grid gap-2">
              {statuses.map((status) => (
                <div key={status.key} className="rounded-lg border border-[var(--line)] bg-[var(--bg-elevated)] p-3">
                  <div className="flex items-center justify-between gap-3">
                    <div className="min-w-0">
                      <div className="truncate text-sm font-semibold text-[var(--text-primary)]">{status.name}</div>
                      <div className="text-xs text-[var(--text-muted)]">{status.scope}</div>
                    </div>
                    <span className={`rounded-full px-2 py-1 text-[10px] uppercase tracking-wider ${statusTone(status.state)}`}>
                      {status.state}
                    </span>
                  </div>
                  <p className="mt-2 text-xs leading-5 text-[var(--text-muted)]">{status.line}</p>
                </div>
              ))}
            </div>
          </RhythmPanel>

          <RhythmPanel title="Proactive observations">
            {observations.map((item) => (
              <div key={item} className="rounded-lg border border-[var(--line)] bg-[var(--bg-elevated)] p-3 text-sm leading-5 text-[var(--text-primary)]">
                {item}
              </div>
            ))}
          </RhythmPanel>

          <RhythmPanel title="Learned facts">
            {learnings.map((item) => (
              <div key={item} className="rounded-lg border border-[var(--line)] bg-[var(--bg-elevated)] p-3 text-xs leading-5 text-[var(--text-muted)]">
                {item}
              </div>
            ))}
          </RhythmPanel>
        </div>
      </div>
    </section>
  );
}

function RhythmPanel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-xl border border-[var(--line)] bg-white/65 p-3">
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)]">
        {title}
      </h3>
      <div className="grid gap-2">{children}</div>
    </section>
  );
}

function Empty({ text }: { text: string }) {
  return (
    <div className="rounded-lg border border-dashed border-[var(--line)] p-3 text-xs leading-5 text-[var(--text-muted)]">
      {text}
    </div>
  );
}

function filterEvents(events: ActivityEvent[], window: WindowKey): ActivityEvent[] {
  const cutoff = cutoffFor(window);
  return events.filter((event) => new Date(event.ts).getTime() >= cutoff).slice(0, 30);
}

function filterArtifacts(artifacts: AgilePanel["artifacts"], window: WindowKey) {
  const cutoff = cutoffFor(window);
  return artifacts.filter((artifact) => new Date(artifact.created_at).getTime() >= cutoff);
}

function cutoffFor(window: WindowKey): number {
  const now = new Date();
  if (window === "today") {
    const start = new Date(now);
    start.setHours(0, 0, 0, 0);
    return start.getTime();
  }
  if (window === "week") {
    const start = new Date(now);
    start.setDate(now.getDate() - 7);
    return start.getTime();
  }
  const start = new Date(now);
  start.setDate(now.getDate() - 14);
  return start.getTime();
}

function buildAgentStatuses(agents: AgentState[]) {
  return agents
    .filter((agent) => agent.last_event_at || agent.in_flight)
    .sort((a, b) => {
      if (a.in_flight !== b.in_flight) return a.in_flight ? -1 : 1;
      return (
        new Date(b.last_event_at ?? 0).getTime() -
        new Date(a.last_event_at ?? 0).getTime()
      );
    })
    .map((agent) => {
      const state = agent.in_flight ? "doing now" : agent.last_event === "crew_checkin" ? "available" : "recently";
      const scope = agent.project ?? "shared company bench";
      return {
        key: agent.id,
        name: prettyRole(agent.role),
        scope,
        state,
        line: agent.in_flight
          ? `${prettyRole(agent.role)} is actively working in ${scope}.`
          : agent.last_output
            ? `Recently: ${agent.last_output}`
            : "Ready for the next assignment.",
      };
    });
}

function statusTone(state: string): string {
  if (state === "doing now") return "bg-[var(--accent)]/15 text-[var(--accent)]";
  if (state === "available") return "bg-[var(--state-success)]/15 text-[var(--state-success)]";
  return "bg-[var(--bg-surface)] text-[var(--text-muted)]";
}

function buildLearnings(
  agents: AgentState[],
  events: ActivityEvent[],
  artifacts: AgilePanel["artifacts"],
): string[] {
  const out = new Set<string>();
  for (const artifact of artifacts) {
    if (artifact.blockers.length > 0) {
      out.add(`${artifact.project}: current blocker pattern is "${artifact.blockers[0]}".`);
    }
    if (artifact.next_actions.length > 0) {
      out.add(`${artifact.project}: next useful action is "${artifact.next_actions[0]}".`);
    }
  }
  for (const event of events) {
    if (event.event === "decision_submitted" && event.project) {
      out.add(`${event.project}: the team is generating new Decision candidates.`);
    }
    if (event.event === "crew_finished" && event.project) {
      out.add(`${event.project}: crew runs are completing and adding fresh operating context.`);
    }
  }
  for (const agent of agents) {
    if (agent.last_output && agent.project) {
      out.add(`${agent.project}: ${prettyRole(agent.role)} recently learned "${agent.last_output}".`);
    }
  }
  return [...out];
}

function buildObservations(
  agents: AgentState[],
  events: ActivityEvent[],
  artifacts: AgilePanel["artifacts"],
): string[] {
  const out: string[] = [];
  const active = agents.filter((agent) => agent.in_flight).length;
  const blockers = artifacts.reduce((count, artifact) => count + artifact.blockers.length, 0);
  const approvals = events.filter((event) => event.event === "decision_resolved").length;
  if (active > 0) out.push(`${active} agent lane${active === 1 ? " is" : "s are"} active right now.`);
  if (blockers > 0) out.push(`${blockers} blocker${blockers === 1 ? "" : "s"} surfaced in recent rituals; Delivery should keep those visible.`);
  if (approvals > 0) out.push(`${approvals} Decision update${approvals === 1 ? "" : "s"} landed in this window; watch Sprint Board pickup.`);
  if (out.length === 0) out.push("No urgent anomaly surfaced in this window; the company is mostly in monitoring and planning mode.");
  return out;
}

