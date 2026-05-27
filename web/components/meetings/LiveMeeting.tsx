"use client";

import { useEffect, useMemo, useReducer, useRef } from "react";
import type { MeetingDetail, MeetingTurn, Seat } from "@/lib/schemas";
import { RoundTable } from "./RoundTable";

/**
 * Client wrapper that subscribes to `/api/meetings/{run_id}/stream` via
 * EventSource and re-renders as new turns arrive.
 *
 * State is reducer-driven so each SSE event maps to one action:
 *   - `init`      → replace the whole meeting (used on reconnect too)
 *   - `turn`      → append a turn + flip is_speaking_now to the new role
 *   - `heartbeat` → bump the lastHeartbeat clock (used for the "live" pulse)
 *
 * The component degrades gracefully when EventSource is unavailable (SSR,
 * older browsers): it just renders the initial server-fetched state.
 */
export function LiveMeeting({ initial }: { initial: MeetingDetail }) {
  const [state, dispatch] = useReducer(meetingReducer, initial, init);
  const recentTurnSeqRef = useRef<number>(
    initial.turns.length > 0 ? initial.turns[initial.turns.length - 1].sequence : -1,
  );

  useEffect(() => {
    // Only live meetings need SSE; replays of completed crew runs are static.
    if (initial.status !== "in_progress") return;
    if (typeof window === "undefined" || typeof EventSource === "undefined") return;

    const es = new EventSource(`/api/meetings/${initial.run_id}/stream`);
    es.addEventListener("init", (e) => {
      try {
        const parsed = JSON.parse((e as MessageEvent).data) as MeetingDetail;
        recentTurnSeqRef.current =
          parsed.turns.length > 0
            ? parsed.turns[parsed.turns.length - 1].sequence
            : -1;
        dispatch({ kind: "init", meeting: parsed });
      } catch (err) {
        console.error("init parse failed", err);
      }
    });
    es.addEventListener("turn", (e) => {
      try {
        const turn = JSON.parse((e as MessageEvent).data) as MeetingTurn;
        if (turn.sequence <= recentTurnSeqRef.current) return; // dedupe
        recentTurnSeqRef.current = turn.sequence;
        dispatch({ kind: "turn", turn });
      } catch (err) {
        console.error("turn parse failed", err);
      }
    });
    es.addEventListener("heartbeat", () => dispatch({ kind: "heartbeat" }));
    es.addEventListener("error", () => {
      // EventSource auto-reconnects. Surface the state to the UI so the
      // operator can see "live" vs "reconnecting" in the header.
      dispatch({ kind: "transport_error" });
    });
    return () => {
      es.close();
    };
  }, [initial.run_id, initial.status]);

  const isLive = state.meeting.status === "in_progress";
  const transportLabel = useMemo(() => {
    if (!isLive) return null;
    return state.transportError ? "reconnecting…" : "live";
  }, [isLive, state.transportError]);

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <MeetingHeader
        meeting={state.meeting}
        transportLabel={transportLabel}
        lastHeartbeatAt={state.lastHeartbeatAt}
      />

      <section className="rounded-xl border border-[var(--line)] bg-[var(--bg-surface)] p-6">
        <RoundTable
          seats={state.meeting.seats}
          multiAgent={state.meeting.multi_agent}
          size="lg"
        />
        {state.meeting.latest_turn && (
          <LatestTurnPanel
            turn={state.meeting.latest_turn}
            seats={state.meeting.seats}
          />
        )}
      </section>

      <section>
        <h2 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-[var(--text-muted)]">
          Transcript ({state.meeting.turns.length})
        </h2>
        <ol className="space-y-3">
          {state.meeting.turns.map((turn) => (
            <li
              key={turn.sequence}
              className="rounded-lg border border-[var(--line)] bg-[var(--bg-canvas)] p-3"
            >
              <div className="flex items-center gap-2 text-[11px] text-[var(--text-muted)]">
                <span className="font-mono text-[var(--text-primary)]">
                  {turn.agent_display_name ?? turn.agent_role}
                </span>
                <span>·</span>
                <span className="uppercase tracking-wider">{turn.role_in_conversation}</span>
                <span>·</span>
                <span>#{turn.sequence}</span>
                <span>·</span>
                <span>{new Date(turn.created_at).toLocaleTimeString()}</span>
              </div>
              <p className="mt-2 whitespace-pre-wrap text-sm leading-relaxed text-[var(--text-primary)]">
                {turn.content_full}
              </p>
            </li>
          ))}
        </ol>
      </section>
    </div>
  );
}

function MeetingHeader({
  meeting,
  transportLabel,
  lastHeartbeatAt,
}: {
  meeting: MeetingDetail;
  transportLabel: string | null;
  lastHeartbeatAt: number;
}) {
  const statusClass =
    meeting.status === "in_progress"
      ? "bg-[var(--state-success)]/15 text-[var(--state-success)]"
      : meeting.status === "failed"
        ? "bg-[var(--state-danger)]/15 text-[var(--state-danger)]"
        : "bg-[var(--bg-canvas)] text-[var(--text-muted)]";
  return (
    <header>
      <div className="flex items-center gap-2">
        <h1 className="text-lg font-semibold text-[var(--text-primary)]">
          {meeting.ritual_label}
        </h1>
        {meeting.project && (
          <span className="rounded bg-[var(--bg-canvas)] px-1.5 py-0.5 font-mono text-[11px] text-[var(--text-muted)]">
            {meeting.project}
          </span>
        )}
        <span className={`rounded px-1.5 py-0.5 text-[10px] uppercase tracking-wider ${statusClass}`}>
          {meeting.status === "in_progress" ? "live" : meeting.status}
        </span>
        {transportLabel && (
          <span className="text-[10px] text-[var(--text-muted)]">
            · {transportLabel}
          </span>
        )}
      </div>
      <p className="mt-1 text-sm text-[var(--text-muted)]">{meeting.ritual_agenda}</p>
      <div className="mt-2 flex gap-4 font-mono text-[10px] text-[var(--text-muted)]">
        <span>run · {meeting.run_id.slice(0, 12)}</span>
        <span>started · {new Date(meeting.started_at).toLocaleString()}</span>
        <span>turns · {meeting.total_turns}</span>
        {lastHeartbeatAt > 0 && meeting.status === "in_progress" && (
          <span title="Last SSE heartbeat received from server">
            last beat · {Math.max(0, Math.round((Date.now() - lastHeartbeatAt) / 1000))}s ago
          </span>
        )}
      </div>
    </header>
  );
}

function LatestTurnPanel({ turn, seats }: { turn: MeetingTurn; seats: Seat[] }) {
  const speakerSeat = seats.find((s) => s.agent_role === turn.agent_role);
  const display = speakerSeat?.agent_display_name ?? turn.agent_display_name ?? turn.agent_role;
  return (
    <div className="mt-6 rounded-lg border border-[var(--accent)]/30 bg-[var(--accent)]/5 p-4">
      <div className="text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
        Latest turn ·{" "}
        <span className="font-mono text-[var(--text-primary)]">{display}</span>{" "}
        · {turn.role_in_conversation}
      </div>
      <p className="mt-2 whitespace-pre-wrap text-sm leading-relaxed text-[var(--text-primary)]">
        {turn.content_preview}
      </p>
    </div>
  );
}

// ---------- Reducer ----------

type Action =
  | { kind: "init"; meeting: MeetingDetail }
  | { kind: "turn"; turn: MeetingTurn }
  | { kind: "heartbeat" }
  | { kind: "transport_error" };

interface State {
  meeting: MeetingDetail;
  lastHeartbeatAt: number;
  transportError: boolean;
}

function init(initial: MeetingDetail): State {
  return { meeting: initial, lastHeartbeatAt: Date.now(), transportError: false };
}

function meetingReducer(state: State, action: Action): State {
  switch (action.kind) {
    case "init":
      return { meeting: action.meeting, lastHeartbeatAt: Date.now(), transportError: false };
    case "turn": {
      const newTurns = [...state.meeting.turns, action.turn];
      // Flip is_speaking_now to the new turn's seat.
      const newSeats: Seat[] = state.meeting.seats.map((seat) => ({
        ...seat,
        is_speaking_now: seat.agent_role === action.turn.agent_role,
      }));
      return {
        meeting: {
          ...state.meeting,
          turns: newTurns,
          latest_turn: action.turn,
          total_turns: state.meeting.total_turns + 1,
          seats: newSeats,
          last_event_at: action.turn.created_at,
        },
        lastHeartbeatAt: Date.now(),
        transportError: false,
      };
    }
    case "heartbeat":
      return { ...state, lastHeartbeatAt: Date.now(), transportError: false };
    case "transport_error":
      return { ...state, transportError: true };
  }
}
