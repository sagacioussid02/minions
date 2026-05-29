"use client";

import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import type { MeetingDetail, MeetingTurn, Seat } from "@/lib/schemas";

/**
 * Headless meeting feed — the single source of truth shared by the 2D
 * (`LiveMeeting`) and 3D (`Meeting3D`) renderers.
 *
 * It owns the same two feeds the 2D view always used:
 *   - LIVE (status=in_progress): Server-Sent Events from
 *     `/api/meetings/{run_id}/stream`. Turns arrive as they happen.
 *   - REPLAY (status=completed|failed): a controlled timer reveals the
 *     pre-loaded turns one-by-one. No network calls after initial load.
 *
 * Both feeds funnel through the same reducer that derives the "visible"
 * meeting. Because both renderers consume this one hook, the 3D view has
 * identical latency characteristics to the 2D view — same event, same
 * dispatch, just a different draw target.
 */

export type ReplaySpeed = 0.5 | 1 | 2 | 4;

export interface ReplayControls {
  playing: boolean;
  speed: ReplaySpeed;
  revealed: number;
  total: number;
  onPlayPause: () => void;
  onRestart: () => void;
  onSkipToEnd: () => void;
  onSpeed: (s: ReplaySpeed) => void;
}

export interface MeetingFeed {
  /** Progressively-revealed meeting state (seats + turns shown so far). */
  meeting: MeetingDetail;
  /** True for in_progress meetings driven by the SSE stream. */
  isLive: boolean;
  /** Sequence of the most-recently-revealed turn (-1 when none). Drives
   *  the "new turn" animation in both renderers. */
  lastRevealedSequence: number;
  /** "live" / "reconnecting…" for live runs, null otherwise. */
  transportLabel: string | null;
  /** Epoch ms of the last SSE heartbeat/turn — for the header staleness chip. */
  lastHeartbeatAt: number;
  /** Whether the live transport is currently in an error/reconnecting state. */
  transportError: boolean;
  /** Replay control surface (only meaningful for non-live meetings). */
  replay: ReplayControls;
}

export function useMeetingFeed(initial: MeetingDetail): MeetingFeed {
  const isLive = initial.status === "in_progress";
  const totalTurns = initial.turns.length;

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

  const lastRevealedSequence =
    state.meeting.turns.length > 0
      ? state.meeting.turns[state.meeting.turns.length - 1].sequence
      : -1;

  const replay = useMemo<ReplayControls>(
    () => ({
      playing: replayPlaying,
      speed: replaySpeed,
      revealed: state.meeting.turns.length,
      total: totalTurns,
      onPlayPause: () => setReplayPlaying((p) => !p),
      onRestart: replayRestart,
      onSkipToEnd: replaySkipToEnd,
      onSpeed: setReplaySpeed,
    }),
    [
      replayPlaying,
      replaySpeed,
      state.meeting.turns.length,
      totalTurns,
      replayRestart,
      replaySkipToEnd,
    ],
  );

  return {
    meeting: state.meeting,
    isLive,
    lastRevealedSequence,
    transportLabel,
    lastHeartbeatAt: state.lastHeartbeatAt,
    transportError: state.transportError,
    replay,
  };
}

// 1x = one turn every 2.5s. Feels conversational without dragging.
export function baseIntervalForSpeed(speed: ReplaySpeed): number {
  const baseMs = 2500;
  return Math.max(150, baseMs / speed);
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
  // broken before play) but the turns + bubbles cleared.
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
