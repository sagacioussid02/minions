"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { AgentState, MeetingDetail, MeetingSummary } from "@/lib/schemas";
import { agentSeedFor, prettyRole } from "@/lib/roles";
import { Avatar } from "@/components/Avatar";
import { RoundTable } from "@/components/meetings/RoundTable";
import { LiveMeeting } from "@/components/meetings/LiveMeeting";
import { Meeting3DClient } from "@/components/meetings/Meeting3DClient";

/**
 * Homepage "Shared company bench + Meetings" section.
 *
 * Left: a compact glance at the shared (non-project) bench — the executives
 * and floating specialists any project can pull in. Right: a scrollable list
 * of recent crew meetings; picking one loads its detail *inline* with a
 * 2D / 3D toggle, so the operator never leaves the homepage to watch a
 * ritual. The dedicated /hq/meetings routes still exist for deep links.
 */
export function CompanyBenchMeetings({
  agents,
  meetings,
}: {
  agents: AgentState[];
  meetings: MeetingSummary[];
}) {
  const bench = useMemo(
    () => agents.filter((a) => a.project == null),
    [agents],
  );

  return (
    <section className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(240px,320px)_1fr]">
      <SharedBenchPanel members={bench} />
      <MeetingsPanel meetings={meetings} />
    </section>
  );
}

/* ------------------------------------------------------------------ bench */

function SharedBenchPanel({ members }: { members: AgentState[] }) {
  return (
    <div className="rounded-xl border border-[var(--line)] bg-[var(--bg-surface)] p-4">
      <header className="mb-3 flex items-baseline justify-between">
        <h2 className="text-sm font-semibold tracking-tight text-[var(--text-primary)]">
          Shared company bench
        </h2>
        <span className="text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
          {members.length}
        </span>
      </header>
      {members.length === 0 ? (
        <p className="text-xs text-[var(--text-muted)]">No shared agents active.</p>
      ) : (
        <ul className="space-y-1.5">
          {members.map((a) => (
            <BenchMember key={a.id} agent={a} />
          ))}
        </ul>
      )}
    </div>
  );
}

function BenchMember({ agent }: { agent: AgentState }) {
  const ring = `var(--color-role-${agent.role_tier})`;
  return (
    <li className="flex items-center gap-2.5">
      <Avatar
        seed={agentSeedFor(agent.role, agent.project)}
        size={26}
        ring={ring}
        mood={agent.in_flight ? "active" : "idle"}
      />
      <div className="min-w-0 flex-1">
        <div className="truncate text-xs font-medium text-[var(--text-primary)]">
          {agent.display_name ?? prettyRole(agent.role)}
        </div>
        <div className="truncate text-[10px] text-[var(--text-muted)]">
          {prettyRole(agent.role)}
        </div>
      </div>
      <span
        className="h-1.5 w-1.5 shrink-0 rounded-full"
        title={agent.in_flight ? "working" : agent.errored ? "errored" : "idle"}
        style={{
          backgroundColor: agent.errored
            ? "var(--state-danger)"
            : agent.in_flight
              ? "var(--state-success)"
              : "var(--line)",
        }}
      />
    </li>
  );
}

/* --------------------------------------------------------------- meetings */

function MeetingsPanel({ meetings }: { meetings: MeetingSummary[] }) {
  const [selected, setSelected] = useState<string | null>(null);

  if (selected) {
    return <MeetingDetailPanel runId={selected} onBack={() => setSelected(null)} />;
  }

  const liveFirst = (a: MeetingSummary, b: MeetingSummary) =>
    Number(b.status === "in_progress") - Number(a.status === "in_progress");
  const ordered = [...meetings].sort(liveFirst);

  return (
    <div className="flex flex-col rounded-xl border border-[var(--line)] bg-[var(--bg-surface)]">
      <header className="flex items-baseline justify-between border-b border-[var(--line)] px-4 py-2.5">
        <h2 className="text-sm font-semibold tracking-tight text-[var(--text-primary)]">
          Meetings
        </h2>
        <span className="text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
          last 7 days · {meetings.length}
        </span>
      </header>
      {ordered.length === 0 ? (
        <p className="p-6 text-center text-xs text-[var(--text-muted)]">
          No meetings in the last 7 days.
        </p>
      ) : (
        <ul className="max-h-[68vh] divide-y divide-[var(--line)] overflow-y-auto">
          {ordered.map((m) => (
            <MeetingRow key={m.run_id} meeting={m} onOpen={() => setSelected(m.run_id)} />
          ))}
        </ul>
      )}
    </div>
  );
}

function MeetingRow({
  meeting,
  onOpen,
}: {
  meeting: MeetingSummary;
  onOpen: () => void;
}) {
  const live = meeting.status === "in_progress";
  return (
    <li>
      <button
        type="button"
        onClick={onOpen}
        className="flex w-full items-center gap-3 px-4 py-3 text-left transition hover:bg-[var(--bg-elevated)]"
      >
        <div className="w-[64px] shrink-0">
          <RoundTable seats={meeting.seats} multiAgent={meeting.multi_agent} size="sm" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-sm font-medium text-[var(--text-primary)]">
              {meeting.ritual_label}
            </span>
            {meeting.project && (
              <span className="shrink-0 rounded bg-[var(--bg-canvas)] px-1.5 py-0.5 font-mono text-[10px] text-[var(--text-muted)]">
                {meeting.project}
              </span>
            )}
            <span
              className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] uppercase tracking-wider ${
                live
                  ? "bg-[var(--state-success)]/15 text-[var(--state-success)]"
                  : meeting.status === "failed"
                    ? "bg-[var(--state-danger)]/15 text-[var(--state-danger)]"
                    : "bg-[var(--bg-canvas)] text-[var(--text-muted)]"
              }`}
            >
              {live ? "live" : meeting.status}
            </span>
          </div>
          <p className="mt-0.5 truncate text-xs text-[var(--text-muted)]">
            {meeting.ritual_agenda}
          </p>
        </div>
        <span className="shrink-0 font-mono text-[10px] text-[var(--text-muted)]">
          {meeting.total_turns} turn{meeting.total_turns === 1 ? "" : "s"}
        </span>
      </button>
    </li>
  );
}

/* ---------------------------------------------------------- inline detail */

async function fetchMeeting(runId: string): Promise<MeetingDetail> {
  const r = await fetch(`/api/meetings/${runId}`, { cache: "no-store" });
  if (!r.ok) throw new Error("meeting fetch failed");
  return r.json();
}

function MeetingDetailPanel({
  runId,
  onBack,
}: {
  runId: string;
  onBack: () => void;
}) {
  const [view, setView] = useState<"2d" | "3d">("2d");
  const q = useQuery({
    queryKey: ["meeting", runId],
    queryFn: () => fetchMeeting(runId),
    refetchInterval: 5_000,
  });

  return (
    <div className="rounded-xl border border-[var(--line)] bg-[var(--bg-surface)]">
      <header className="flex items-center justify-between border-b border-[var(--line)] px-4 py-2.5">
        <button
          type="button"
          onClick={onBack}
          className="text-xs text-[var(--text-muted)] transition hover:text-[var(--text-primary)]"
        >
          ← Meetings
        </button>
        <div className="flex overflow-hidden rounded-md border border-[var(--line)]">
          {(["2d", "3d"] as const).map((v) => (
            <button
              key={v}
              type="button"
              onClick={() => setView(v)}
              className={`px-2.5 py-1 text-[10px] uppercase tracking-wider transition ${
                view === v
                  ? "bg-[var(--accent)]/15 text-[var(--accent)]"
                  : "text-[var(--text-muted)] hover:text-[var(--text-primary)]"
              }`}
            >
              {v}
            </button>
          ))}
        </div>
      </header>
      <div className="p-3 lg:p-4">
        {q.isPending ? (
          <div className="flex h-[40vh] items-center justify-center text-xs text-[var(--text-muted)]">
            Loading meeting…
          </div>
        ) : q.isError || !q.data ? (
          <div className="flex h-[40vh] items-center justify-center text-xs text-[var(--state-danger)]">
            Could not load this meeting.
          </div>
        ) : view === "2d" ? (
          <LiveMeeting initial={q.data} />
        ) : (
          <Meeting3DClient initial={q.data} backHref="/hq/meetings" />
        )}
      </div>
    </div>
  );
}
