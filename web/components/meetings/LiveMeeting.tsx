"use client";

import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import type { MeetingDetail, MeetingTurn, Seat } from "@/lib/schemas";
import { humanize } from "@/lib/meetings/format";
import { RoundTable } from "./RoundTable";

/**
 * Client wrapper that drives the meeting room from two possible feeds:
 *   - LIVE (status=in_progress): Server-Sent Events stream from
 *     `/api/meetings/{run_id}/stream`. Turns arrive as they happen.
 *   - REPLAY (status=completed|failed): a controlled timer reveals the
 *     pre-loaded turns one-by-one so the operator can re-watch the
 *     meeting unfold as if it were live. No real timestamps used —
 *     just the conversation order.
 *
 * Both feeds funnel through the same reducer that derives the "visible"
 * meeting state. From the round-table's point of view, a replay turn
 * lands exactly like a live SSE turn: the speaker's bubble pops, the
 * halo flips to them, transcript fades in at the top.
 */
export function LiveMeeting({ initial }: { initial: MeetingDetail }) {
  const isLive = initial.status === "in_progress";
  const totalTurns = initial.turns.length;

  // The reducer's state holds the "full" meeting we'll progressively
  // reveal. For live runs it starts with the SSE-fed history (typically
  // empty until init lands); for replays it starts empty and the
  // controlled timer pushes turns from `initial.turns`.
  const [state, dispatch] = useReducer(
    meetingReducer,
    { initial, startEmpty: !isLive },
    init,
  );

  // ---- LIVE feed: SSE ----
  const recentTurnSeqRef = useRef<number>(
    isLive && state.meeting.turns.length > 0
      ? state.meeting.turns[state.meeting.turns.length - 1].sequence
      : -1,
  );

  useEffect(() => {
    if (!isLive) return;
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
    es.addEventListener("error", () => dispatch({ kind: "transport_error" }));
    return () => es.close();
  }, [initial.run_id, isLive]);

  // ---- REPLAY feed: controlled timer ----
  const [replaySpeed, setReplaySpeed] = useState<ReplaySpeed>(1);
  const [replayPlaying, setReplayPlaying] = useState(false);
  const replayCursorRef = useRef<number>(0);

  // When the operator hits "play" on a completed meeting, advance the
  // visible-turn cursor on a clock. Speed determines the interval.
  useEffect(() => {
    if (isLive || !replayPlaying) return;
    const intervalMs = baseIntervalForSpeed(replaySpeed);
    const id = setInterval(() => {
      const next = replayCursorRef.current + 1;
      if (next > totalTurns) {
        setReplayPlaying(false);
        return;
      }
      const turn = initial.turns[next - 1];
      replayCursorRef.current = next;
      dispatch({ kind: "turn", turn });
    }, intervalMs);
    return () => clearInterval(id);
  }, [isLive, replayPlaying, replaySpeed, totalTurns, initial.turns]);

  const replayRestart = useCallback(() => {
    setReplayPlaying(false);
    replayCursorRef.current = 0;
    dispatch({ kind: "reset_to_empty", baseMeeting: initial });
  }, [initial]);

  const replaySkipToEnd = useCallback(() => {
    setReplayPlaying(false);
    replayCursorRef.current = totalTurns;
    dispatch({ kind: "reset_to_full", fullMeeting: initial });
  }, [initial, totalTurns]);

  const transportLabel = useMemo(() => {
    if (!isLive) return null;
    return state.transportError ? "reconnecting…" : "live";
  }, [isLive, state.transportError]);

  const turnsNewestFirst = useMemo(
    () => [...state.meeting.turns].reverse(),
    [state.meeting.turns],
  );

  // The most-recently-revealed turn sequence — used to animate that
  // specific transcript row (both for live SSE arrivals AND replays).
  const lastRevealedSequence =
    state.meeting.turns.length > 0
      ? state.meeting.turns[state.meeting.turns.length - 1].sequence
      : -1;

  return (
    <div className="mx-auto max-w-7xl space-y-4">
      <MeetingHeader
        meeting={state.meeting}
        baseMeeting={initial}
        transportLabel={transportLabel}
        lastHeartbeatAt={state.lastHeartbeatAt}
      />

      {!isLive && totalTurns > 0 && (
        <ReplayControls
          revealed={state.meeting.turns.length}
          total={totalTurns}
          playing={replayPlaying}
          speed={replaySpeed}
          onPlayPause={() => setReplayPlaying((p) => !p)}
          onRestart={replayRestart}
          onSkipToEnd={replaySkipToEnd}
          onSpeed={setReplaySpeed}
        />
      )}

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
              Transcript ({state.meeting.turns.length}
              {!isLive && totalTurns !== state.meeting.turns.length
                ? ` of ${totalTurns}`
                : ""}
              )
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
                  : "Press play to replay the meeting."}
              </li>
            ) : (
              turnsNewestFirst.map((turn) => (
                <TranscriptItem
                  key={turn.sequence}
                  turn={turn}
                  isNew={turn.sequence === lastRevealedSequence}
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

// ---------- Replay UI ----------

type ReplaySpeed = 0.5 | 1 | 2 | 4;

function baseIntervalForSpeed(speed: ReplaySpeed): number {
  // 1x = one turn every 2.5s. Feels conversational without dragging.
  const baseMs = 2500;
  return Math.max(150, baseMs / speed);
}

function ReplayControls({
  revealed,
  total,
  playing,
  speed,
  onPlayPause,
  onRestart,
  onSkipToEnd,
  onSpeed,
}: {
  revealed: number;
  total: number;
  playing: boolean;
  speed: ReplaySpeed;
  onPlayPause: () => void;
  onRestart: () => void;
  onSkipToEnd: () => void;
  onSpeed: (s: ReplaySpeed) => void;
}) {
  const atEnd = revealed >= total;
  return (
    <div className="flex flex-wrap items-center gap-3 rounded-xl border border-[var(--accent)]/30 bg-[var(--accent)]/5 px-3 py-2">
      <div className="text-[10px] font-semibold uppercase tracking-wider text-[var(--accent)]">
        Replay
      </div>
      <div className="flex items-center gap-1.5">
        <button
          type="button"
          onClick={onRestart}
          disabled={revealed === 0}
          title="Restart from the first turn"
          className="rounded border border-[var(--line)] bg-[var(--bg-elevated)] px-2 py-0.5 text-xs text-[var(--text-primary)] disabled:cursor-not-allowed disabled:opacity-40 hover:border-[var(--accent)]/60"
        >
          ⏮
        </button>
        <button
          type="button"
          onClick={onPlayPause}
          disabled={atEnd}
          title={playing ? "Pause replay" : "Play replay"}
          className="rounded border border-[var(--accent)] bg-[var(--accent)] px-2.5 py-0.5 text-xs font-medium text-white disabled:cursor-not-allowed disabled:opacity-40"
        >
          {playing ? "⏸ Pause" : atEnd ? "Done" : "▶ Play"}
        </button>
        <button
          type="button"
          onClick={onSkipToEnd}
          disabled={atEnd}
          title="Skip to the end of the meeting"
          className="rounded border border-[var(--line)] bg-[var(--bg-elevated)] px-2 py-0.5 text-xs text-[var(--text-primary)] disabled:cursor-not-allowed disabled:opacity-40 hover:border-[var(--accent)]/60"
        >
          ⏭
        </button>
      </div>
      <div className="flex items-center gap-1 text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
        Speed:
        {([0.5, 1, 2, 4] as const).map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => onSpeed(s)}
            className={`rounded px-1.5 py-0.5 font-mono ${
              s === speed
                ? "bg-[var(--accent)] text-white"
                : "bg-[var(--bg-elevated)] text-[var(--text-primary)] hover:bg-[var(--bg-canvas)]"
            }`}
          >
            {s}×
          </button>
        ))}
      </div>
      <div className="ml-auto font-mono text-[10px] text-[var(--text-muted)]">
        {revealed} / {total}
      </div>
    </div>
  );
}

// ---------- Transcript item ----------

function TranscriptItem({ turn, isNew }: { turn: MeetingTurn; isNew: boolean }) {
  const { preview, body, isJson } = humanize(turn.content_full);
  const [expanded, setExpanded] = useState(false);
  const canExpand = isJson || body !== preview;
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
        {isJson && (
          <>
            <span>·</span>
            <span className="rounded bg-[var(--accent)]/10 px-1 text-[var(--accent)]">
              structured
            </span>
          </>
        )}
      </div>
      {expanded ? (
        <pre className="mt-1.5 max-h-80 overflow-auto whitespace-pre-wrap rounded bg-[var(--bg-elevated)] p-2 font-mono text-[10px] leading-snug text-[var(--text-primary)]">
          {body}
        </pre>
      ) : (
        <p className="mt-1.5 text-[11px] leading-snug text-[var(--text-primary)]">
          {preview}
        </p>
      )}
      {canExpand && (
        <button
          type="button"
          onClick={() => setExpanded((x) => !x)}
          className="mt-1 text-[10px] text-[var(--accent)] hover:underline"
        >
          {expanded ? "collapse" : isJson ? "show raw JSON" : "show full"}
        </button>
      )}
    </li>
  );
}

// ---------- Header ----------

function MeetingHeader({
  meeting,
  baseMeeting,
  transportLabel,
  lastHeartbeatAt,
}: {
  meeting: MeetingDetail;
  baseMeeting: MeetingDetail;
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
        <span>started · {new Date(baseMeeting.started_at).toLocaleString()}</span>
        <span>turns · {baseMeeting.total_turns}</span>
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
  | { kind: "transport_error" }
  | { kind: "reset_to_empty"; baseMeeting: MeetingDetail }
  | { kind: "reset_to_full"; fullMeeting: MeetingDetail };

interface State {
  meeting: MeetingDetail;
  lastHeartbeatAt: number;
  transportError: boolean;
}

function init({
  initial,
  startEmpty,
}: {
  initial: MeetingDetail;
  startEmpty: boolean;
}): State {
  // Replays start with the round-table seats laid out (so it doesn't look
  // broken before play) but the turns + bubbles cleared. We derive the
  // seat skeleton from the loaded data and zero out their bubble content
  // + speaking flag.
  if (!startEmpty) {
    return {
      meeting: initial,
      lastHeartbeatAt: Date.now(),
      transportError: false,
    };
  }
  const cleanedSeats: Seat[] = initial.seats.map((s) => ({
    ...s,
    is_speaking_now: false,
    last_turn_preview: null,
    last_turn_sequence: null,
  }));
  return {
    meeting: {
      ...initial,
      turns: [],
      latest_turn: null,
      total_turns: 0,
      seats: cleanedSeats,
    },
    lastHeartbeatAt: Date.now(),
    transportError: false,
  };
}

function meetingReducer(state: State, action: Action): State {
  switch (action.kind) {
    case "init":
      return {
        meeting: action.meeting,
        lastHeartbeatAt: Date.now(),
        transportError: false,
      };
    case "turn": {
      const newTurns = [...state.meeting.turns, action.turn];
      const newSeats: Seat[] = state.meeting.seats.map((seat) => {
        if (seat.agent_role !== action.turn.agent_role) {
          return { ...seat, is_speaking_now: false };
        }
        return {
          ...seat,
          is_speaking_now: true,
          last_turn_preview:
            action.turn.content_preview || action.turn.content_full || seat.last_turn_preview,
          last_turn_sequence: action.turn.sequence,
          agent_display_name:
            seat.agent_display_name ?? action.turn.agent_display_name ?? null,
        };
      });
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
          total_turns: newTurns.length,
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
    case "reset_to_empty":
      return init({ initial: action.baseMeeting, startEmpty: true });
    case "reset_to_full":
      return init({ initial: action.fullMeeting, startEmpty: false });
  }
}
