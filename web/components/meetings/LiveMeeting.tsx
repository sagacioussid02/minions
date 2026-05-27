"use client";

import { useEffect, useMemo, useReducer, useRef } from "react";
import type { MeetingDetail, MeetingTurn, Seat } from "@/lib/schemas";
import { RoundTable } from "./RoundTable";

/**
 * Client wrapper that subscribes to `/api/meetings/{run_id}/stream` via
 * EventSource and re-renders as new turns arrive.
 *
 * Layout (operator request 2026-05-27): round-table on the LEFT, live
 * transcript stream on the RIGHT — not below. New turns fade in at the
 * top of the stream so the operator's eye is naturally drawn to fresh
 * activity without scrolling. Per-seat chat bubbles float radially
 * outside the table showing each agent's most recent line; they pop on
 * change to make the simulation feel lively.
 */
export function LiveMeeting({ initial }: { initial: MeetingDetail }) {
  const [state, dispatch] = useReducer(meetingReducer, initial, init);
  const recentTurnSeqRef = useRef<number>(
    initial.turns.length > 0 ? initial.turns[initial.turns.length - 1].sequence : -1,
  );

  useEffect(() => {
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
        if (turn.sequence <= recentTurnSeqRef.current) return;
        recentTurnSeqRef.current = turn.sequence;
        dispatch({ kind: "turn", turn });
      } catch (err) {
        console.error("turn parse failed", err);
      }
    });
    es.addEventListener("heartbeat", () => dispatch({ kind: "heartbeat" }));
    es.addEventListener("error", () => {
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

  // Turns rendered newest-first in the stream panel — the operator's eye
  // lands on fresh activity without scrolling.
  const turnsNewestFirst = useMemo(
    () => [...state.meeting.turns].reverse(),
    [state.meeting.turns],
  );

  return (
    <div className="mx-auto max-w-7xl space-y-4">
      <MeetingHeader
        meeting={state.meeting}
        transportLabel={transportLabel}
        lastHeartbeatAt={state.lastHeartbeatAt}
      />

      {/* Two-column desktop layout: round-table left, transcript stream right.
          Stacks on narrow viewports so mobile still works. */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1.4fr_1fr]">
        <section className="rounded-xl border border-[var(--line)] bg-[var(--bg-surface)] p-4 lg:p-6">
          <RoundTable
            seats={state.meeting.seats}
            multiAgent={state.meeting.multi_agent}
            size="lg"
          />
        </section>

        <aside className="rounded-xl border border-[var(--line)] bg-[var(--bg-surface)]">
          <div className="flex items-center justify-between border-b border-[var(--line)] px-4 py-2">
            <div className="text-[10px] font-semibold uppercase tracking-wider text-[var(--text-muted)]">
              Transcript ({state.meeting.turns.length})
            </div>
            {isLive && (
              <div className="flex items-center gap-1.5 text-[10px] text-[var(--text-muted)]">
                <span
                  className={`inline-block h-1.5 w-1.5 rounded-full ${
                    state.transportError
                      ? "bg-[var(--state-warn)]"
                      : "bg-[var(--state-success)]"
                  }`}
                />
                {state.transportError ? "reconnecting" : "streaming"}
              </div>
            )}
          </div>
          <ol className="max-h-[600px] space-y-2 overflow-y-auto p-3">
            {turnsNewestFirst.length === 0 ? (
              <li className="rounded-lg border border-dashed border-[var(--line)] p-4 text-center text-xs text-[var(--text-muted)]">
                {isLive
                  ? "Waiting for the first turn…"
                  : "No turns recorded for this meeting."}
              </li>
            ) : (
              turnsNewestFirst.map((turn, idx) => (
                <TranscriptItem
                  key={turn.sequence}
                  turn={turn}
                  // Animate just the freshest turn (idx 0). Replaying an
                  // existing meeting also animates the first item, which is
                  // fine — it draws the eye to where streaming would land.
                  isNew={idx === 0 && turn.sequence === recentTurnSeqRef.current}
                />
              ))
            )}
          </ol>
        </aside>
      </div>

      <style jsx>{`
        :global(.transcript-new) {
          animation: turn-in 0.35s ease-out;
          transform-origin: top center;
        }
        @keyframes turn-in {
          0% {
            opacity: 0;
            transform: translateY(-6px) scale(0.98);
          }
          100% {
            opacity: 1;
            transform: translateY(0) scale(1);
          }
        }
      `}</style>
    </div>
  );
}

function TranscriptItem({ turn, isNew }: { turn: MeetingTurn; isNew: boolean }) {
  return (
    <li
      className={`rounded-lg border border-[var(--line)] bg-[var(--bg-canvas)] p-2.5 ${
        isNew ? "transcript-new border-[var(--accent)]/40" : ""
      }`}
    >
      <div className="flex flex-wrap items-center gap-1.5 text-[10px] text-[var(--text-muted)]">
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
      <p className="mt-1.5 whitespace-pre-wrap text-[11px] leading-snug text-[var(--text-primary)]">
        {turn.content_full}
      </p>
    </li>
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
          <span className="text-[10px] text-[var(--text-muted)]">· {transportLabel}</span>
        )}
      </div>
      <p className="mt-1 text-sm text-[var(--text-muted)]">{meeting.ritual_agenda}</p>
      <div className="mt-2 flex flex-wrap gap-4 font-mono text-[10px] text-[var(--text-muted)]">
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
      // Update seats:
      //   - is_speaking_now flips to the new turn's role
      //   - that seat's last_turn_preview + last_turn_sequence get the new
      //     turn so the chat bubble re-keys and pops the new content
      //   - other seats keep their existing bubble content (their last line)
      const newSeats: Seat[] = state.meeting.seats.map((seat) => {
        const isSpeakingNow = seat.agent_role === action.turn.agent_role;
        if (!isSpeakingNow) {
          // Other seats — just clear their speaking flag.
          return { ...seat, is_speaking_now: false };
        }
        return {
          ...seat,
          is_speaking_now: true,
          last_turn_preview:
            action.turn.content_preview || action.turn.content_full || seat.last_turn_preview,
          last_turn_sequence: action.turn.sequence,
          // If the agent's display name on the new turn is set and the
          // seat didn't have one yet, fill it in.
          agent_display_name:
            seat.agent_display_name ?? action.turn.agent_display_name ?? null,
        };
      });
      // If the new turn's role isn't yet in the seats list (rare —
      // mid-meeting a new agent joins), append a seat. The aggregator's
      // initial render usually catches this, but defending here too.
      if (!newSeats.some((s) => s.agent_role === action.turn.agent_role)) {
        newSeats.push({
          agent_role: action.turn.agent_role,
          agent_display_name: action.turn.agent_display_name,
          seat_position: "center",
          is_speaking_now: true,
          last_turn_preview: action.turn.content_preview || action.turn.content_full,
          last_turn_sequence: action.turn.sequence,
        });
      }
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
