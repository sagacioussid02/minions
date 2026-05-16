"use client";

import { useIsFetching } from "@tanstack/react-query";

/**
 * Header status indicator. Default state is a slow ambient pulse (system
 * alive). When any TanStack Query is fetching, the dot flashes brighter,
 * giving a real sense that the page is polling for data.
 *
 * Tiny — width is 5px so it does not visually compete with the wordmark.
 */
export function HeartbeatDot() {
  const fetching = useIsFetching();
  const active = fetching > 0;
  const color = active ? "var(--accent)" : "var(--state-success)";

  return (
    <span
      className="inline-flex items-center gap-2 text-[10px] uppercase tracking-wider text-[var(--text-muted)]"
      aria-live="polite"
    >
      <span
        className={`size-1.5 rounded-full heartbeat-dot ${active ? "heartbeat-dot-active" : ""}`}
        style={{ background: color, boxShadow: `0 0 8px ${color}` }}
      />
      live
    </span>
  );
}
