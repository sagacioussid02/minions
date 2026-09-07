"use client";

import { useEffect, useMemo, useReducer } from "react";
import type { AgentState } from "@/lib/schemas";

/**
 * Live presence overlay via SSE (`/api/presence/stream`). Deliberately thin:
 * it does NOT replace the Floor's existing polled `AgentState[]` fetch (that
 * stays the source of truth for cost/seats/recent_events/roster scaffold) —
 * this only tracks per-agent status/detail patches so the UI can flip a card
 * to "blocked"/"reviewing" within seconds instead of waiting up to 3s for
 * the next full poll. See openspec/changes/live-presence-and-capacity.
 */

export interface PresenceFeed {
  /** Latest status/detail/in_flight per agent id, from `init` + deltas. */
  byId: Map<string, Pick<AgentState, "status" | "detail" | "in_flight">>;
  /** "live" / "reconnecting…" while connected, null before the first frame. */
  transportLabel: string | null;
  lastHeartbeatAt: number;
}

type Action =
  | { kind: "init"; agents: AgentState[] }
  | { kind: "update"; agents: AgentState[] }
  | { kind: "heartbeat" }
  | { kind: "transport_error" };

interface State {
  byId: Map<string, Pick<AgentState, "status" | "detail" | "in_flight">>;
  lastHeartbeatAt: number;
  transportError: boolean;
  connected: boolean;
}

function reducer(state: State, action: Action): State {
  switch (action.kind) {
    case "init": {
      const byId = new Map(
        action.agents.map((a) => [a.id, pick(a)] as const),
      );
      return { byId, lastHeartbeatAt: Date.now(), transportError: false, connected: true };
    }
    case "update": {
      const byId = new Map(state.byId);
      for (const a of action.agents) byId.set(a.id, pick(a));
      return { ...state, byId, lastHeartbeatAt: Date.now(), transportError: false };
    }
    case "heartbeat":
      return { ...state, lastHeartbeatAt: Date.now(), transportError: false };
    case "transport_error":
      return { ...state, transportError: true };
  }
}

function pick(a: AgentState): Pick<AgentState, "status" | "detail" | "in_flight"> {
  return { status: a.status, detail: a.detail, in_flight: a.in_flight };
}

export function usePresenceFeed(enabled = true): PresenceFeed {
  const [state, dispatch] = useReducer(reducer, {
    byId: new Map(),
    lastHeartbeatAt: 0,
    transportError: false,
    connected: false,
  });

  useEffect(() => {
    if (!enabled) return;
    if (typeof window === "undefined" || typeof EventSource === "undefined") return;

    const es = new EventSource("/api/presence/stream");
    es.addEventListener("init", (e) => {
      try {
        const agents = JSON.parse((e as MessageEvent).data) as AgentState[];
        dispatch({ kind: "init", agents });
      } catch (err) {
        console.error("presence init parse failed", err);
      }
    });
    es.addEventListener("presence_update", (e) => {
      try {
        const agents = JSON.parse((e as MessageEvent).data) as AgentState[];
        dispatch({ kind: "update", agents });
      } catch (err) {
        console.error("presence update parse failed", err);
      }
    });
    es.addEventListener("heartbeat", () => dispatch({ kind: "heartbeat" }));
    es.addEventListener("error", () => dispatch({ kind: "transport_error" }));
    return () => es.close();
  }, [enabled]);

  const transportLabel = useMemo(() => {
    if (!state.connected) return null;
    return state.transportError ? "reconnecting…" : "live";
  }, [state.connected, state.transportError]);

  return { byId: state.byId, transportLabel, lastHeartbeatAt: state.lastHeartbeatAt };
}
