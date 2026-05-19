"use client";

import { useMemo, useState } from "react";
import { ChatLine } from "@/components/stage/ChatLine";
import { ConversationThread } from "@/components/stage/ConversationThread";
import {
  groupByRun,
  liveStartedEvents,
  projectListForEvents,
} from "@/lib/stage-grouping";
import { type ActivityEvent } from "@/lib/schemas";

export function ChatStream({
  events,
  project,
  windowMinutes,
}: {
  events: ActivityEvent[];
  project: string | null;
  windowMinutes: number;
}) {
  const scopeKey = `${project ?? "all"}:${windowMinutes}`;
  const [pagination, setPagination] = useState({ scopeKey, count: 32 });
  const visibleCount = pagination.scopeKey === scopeKey ? pagination.count : 32;
  const filtered = useMemo(
    () => (project ? events.filter((event) => event.project === project) : events),
    [events, project],
  );
  const live = useMemo(() => liveStartedEvents(filtered), [filtered]);
  const groups = useMemo(() => groupByRun(filtered), [filtered]);
  const visibleGroups = groups.slice(0, visibleCount);
  const remaining = Math.max(0, groups.length - visibleGroups.length);
  const projects = projectListForEvents(events);
  const windowLabel = windowMinutes === 60 ? "Last hour" : `Last ${windowMinutes / 60} hours`;

  return (
    <section className="rounded-lg border border-[var(--line)] bg-[var(--bg-surface)]">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--line)] px-5 py-4">
        <div>
          <h2 className="text-base font-semibold tracking-tight text-[var(--text-primary)]">
            Transcript
          </h2>
          <p className="text-sm text-[var(--text-muted)]">
            {windowLabel} · {filtered.length} event{filtered.length === 1 ? "" : "s"}
            {project ? ` · ${project}` : ""}
          </p>
        </div>
        <div className="text-xs text-[var(--text-muted)]">
          {projects.length} project{projects.length === 1 ? "" : "s"} speaking
        </div>
      </div>

      {live.length > 0 && (
        <div className="border-b border-[var(--line)] bg-[var(--bg-elevated)] px-5 py-4">
          <div className="mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-[var(--accent)]">
            <span className="relative flex size-2">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-[var(--accent)]/50" />
              <span className="relative inline-flex size-2 rounded-full bg-[var(--accent)]" />
            </span>
            Now speaking
          </div>
          <ul className="space-y-2">
            {groupByRun(live).map((group) =>
              group.kind === "run" ? (
                <ConversationThread key={group.run_id} group={group} />
              ) : (
                <ChatLine key={group.event.id} event={group.event} />
              ),
            )}
          </ul>
        </div>
      )}

      <ul className="divide-y divide-[var(--line)]">
        {groups.length === 0 && (
          <li className="px-5 py-12 text-center text-sm text-[var(--text-muted)]">
            Quiet floor — nothing in this window.
          </li>
        )}
        {visibleGroups.map((group) =>
          group.kind === "run" ? (
            <ConversationThread key={`${group.run_id}-${group.startTs}`} group={group} />
          ) : (
            <ChatLine key={group.event.id} event={group.event} />
          ),
        )}
      </ul>
      {remaining > 0 && (
        <div className="border-t border-[var(--line)] bg-[var(--bg-elevated)]/45 px-5 py-4 text-center">
          <button
            type="button"
            onClick={() =>
              setPagination((current) => ({
                scopeKey,
                count: (current.scopeKey === scopeKey ? current.count : 32) + 24,
              }))
            }
            className="rounded-md border border-[var(--line)] bg-white px-3 py-1.5 text-sm font-medium text-[var(--text-primary)] shadow-sm transition hover:border-[var(--accent)]/50"
          >
            Load more
          </button>
          <div className="mt-2 text-xs text-[var(--text-muted)]">
            {remaining} older conversation{remaining === 1 ? "" : "s"} in this window
          </div>
        </div>
      )}
    </section>
  );
}
