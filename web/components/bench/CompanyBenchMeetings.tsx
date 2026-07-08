"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type {
  AgentState,
  MeetingDetail,
  MeetingSummary,
  Seat,
} from "@/lib/schemas";
import { agentSeedFor, prettyRole } from "@/lib/roles";
import { Avatar } from "@/components/Avatar";
import { AgentLabel } from "@/components/AgentLabel";
import { AgentChatPanel } from "@/components/agent-chat/AgentChatPanel";
import { RoundTable } from "@/components/meetings/RoundTable";
import { LiveMeeting } from "@/components/meetings/LiveMeeting";
import { Meeting3DClient } from "@/components/meetings/Meeting3DClient";

/**
 * Homepage "Roster + Meetings" section.
 *
 * Left: a compact, searchable roster of every named agent — clicking anyone
 * opens an overlay chat window to talk to them directly. Right: a scrollable
 * list of recent crew meetings; picking one loads its detail inline with a
 * 2D / 3D toggle, so the operator never leaves the homepage. The dedicated
 * /hq/roster and /hq/meetings routes still exist for deep links.
 */
export function CompanyBenchMeetings({
  agents,
  meetings,
}: {
  agents: AgentState[];
  meetings: MeetingSummary[];
}) {
  const [chatTarget, setChatTarget] = useState<AgentState | null>(null);

  return (
    <section className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(260px,340px)_1fr]">
      <RosterPanel agents={agents} onChat={setChatTarget} />
      <MeetingsPanel meetings={meetings} />
      {chatTarget && (
        <AgentChatPanel agent={chatTarget} onClose={() => setChatTarget(null)} />
      )}
    </section>
  );
}

/* ----------------------------------------------------------------- roster */

function RosterPanel({
  agents,
  onChat,
}: {
  agents: AgentState[];
  onChat: (agent: AgentState) => void;
}) {
  const [search, setSearch] = useState("");

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return agents
      .filter((a) =>
        !q
          ? true
          : (a.display_name ?? "").toLowerCase().includes(q) ||
            a.role.toLowerCase().includes(q) ||
            (a.project ?? "").toLowerCase().includes(q),
      )
      .sort((a, b) => {
        if (a.in_flight !== b.in_flight) return a.in_flight ? -1 : 1;
        const an = a.display_name ?? a.role;
        const bn = b.display_name ?? b.role;
        return an.localeCompare(bn);
      });
  }, [agents, search]);

  return (
    <div className="flex flex-col rounded-xl border border-[var(--line)] bg-[var(--bg-surface)]">
      <header className="flex items-baseline justify-between gap-2 border-b border-[var(--line)] px-4 py-2.5">
        <h2 className="text-sm font-semibold tracking-tight text-[var(--text-primary)]">
          Roster
        </h2>
        <span className="text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
          {filtered.length}/{agents.length} · click to chat
        </span>
      </header>
      <div className="px-3 pt-2.5">
        <input
          type="text"
          placeholder="search name / role / project…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="w-full rounded border border-[var(--line)] bg-transparent px-2 py-1 text-xs outline-none focus:border-[var(--accent)]/60"
        />
      </div>
      {filtered.length === 0 ? (
        <p className="p-6 text-center text-xs text-[var(--text-muted)]">
          No agents match.
        </p>
      ) : (
        <ul className="max-h-[62vh] space-y-0.5 overflow-y-auto p-2">
          {filtered.map((a) => (
            <RosterRow key={a.id} agent={a} onChat={onChat} />
          ))}
        </ul>
      )}
    </div>
  );
}

function RosterRow({
  agent,
  onChat,
}: {
  agent: AgentState;
  onChat: (agent: AgentState) => void;
}) {
  const ring = `var(--color-role-${agent.role_tier})`;
  return (
    <li>
      <button
        type="button"
        onClick={() => onChat(agent)}
        title={`Talk to ${agent.display_name ?? prettyRole(agent.role)}`}
        className="flex w-full items-center gap-2.5 rounded-md px-2 py-1.5 text-left transition hover:bg-[var(--bg-elevated)]"
      >
        <Avatar
          seed={agentSeedFor(agent.role, agent.project)}
          size={26}
          ring={ring}
          mood={agent.in_flight ? "active" : "idle"}
        />
        <div className="min-w-0 flex-1">
          <AgentLabel displayName={agent.display_name} role={agent.role} />
          <div className="truncate text-[10px] text-[var(--text-muted)]">
            {agent.project ?? "portfolio"}
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
      </button>
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
        className="flex w-full items-start gap-3 px-4 py-3 text-left transition hover:bg-[var(--bg-elevated)]"
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
          {/* Meeting metadata: start, duration, attendees, turns. */}
          <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[10px] text-[var(--text-muted)]">
            <span>started · {fmtDateTime(meeting.started_at)}</span>
            <span>
              {live ? "running" : "total"} ·{" "}
              {fmtDuration(meeting.started_at, meeting.last_event_at)}
            </span>
            <span title={attendeeNames(meeting.seats)}>
              attendees · {meeting.seats.length}
            </span>
            <span>turns · {meeting.total_turns}</span>
          </div>
          <div className="mt-0.5 truncate text-[10px] text-[var(--text-muted)]/80">
            {attendeeNames(meeting.seats)}
          </div>
        </div>
      </button>
    </li>
  );
}

function fmtDateTime(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString();
}

function fmtDuration(startIso: string, endIso: string): string {
  const ms = new Date(endIso).getTime() - new Date(startIso).getTime();
  if (Number.isNaN(ms) || ms < 0) return "—";
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rs = s % 60;
  if (m < 60) return rs ? `${m}m ${rs}s` : `${m}m`;
  const h = Math.floor(m / 60);
  const rm = m % 60;
  return rm ? `${h}h ${rm}m` : `${h}h`;
}

function attendeeNames(seats: Seat[]): string {
  const names = seats.map((s) => s.agent_display_name ?? prettyRole(s.agent_role));
  if (names.length === 0) return "—";
  if (names.length <= 3) return names.join(", ");
  return `${names.slice(0, 3).join(", ")} +${names.length - 3}`;
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
