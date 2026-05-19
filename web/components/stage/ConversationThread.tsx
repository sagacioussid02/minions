"use client";

import { useEffect, useState } from "react";
import { Avatar } from "@/components/Avatar";
import { ChatLine } from "@/components/stage/ChatLine";
import { agentsForEvent, formatDuration, type EventGroup } from "@/lib/stage-grouping";
import { agentSeedFor, prettyRole, tierFor } from "@/lib/roles";
import { decisionPhrase } from "@/lib/stage-sentences";

type RunGroup = Extract<EventGroup, { kind: "run" }>;

export function ConversationThread({ group }: { group: RunGroup }) {
  const [open, setOpen] = useState(group.live);
  const now = useTick(15_000);
  const start = group.events[group.events.length - 1];
  const role = group.role ?? group.crew ?? "crew";
  const tier = tierFor(role);
  const agents = agentsForEvent(start);
  const names = agents.length > 0
    ? agents.map((agent) => prettyRole(agent)).join(" + ")
    : prettyRole(role);
  const duration = group.live
    ? formatDuration((now || new Date(group.endTs).getTime()) - new Date(group.startTs).getTime())
    : formatDuration(group.durationMs);
  const phrase = decisionPhrase(start);
  const status = group.failed
    ? "hit a blocker"
    : group.live
      ? "are working"
      : "worked";

  return (
    <li className="row-in border-b border-[var(--line)]">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="flex w-full gap-3 px-5 py-4 text-left transition hover:bg-[var(--bg-elevated)]/50"
      >
        <div className="relative">
          <Avatar
            seed={agentSeedFor(role, group.project)}
            size={40}
            ring={`var(--color-role-${tier})`}
          />
          {group.live && (
            <span className="absolute -right-0.5 -top-0.5 size-3 rounded-full border-2 border-white bg-[var(--accent)]" />
          )}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium text-[var(--text-primary)]">{names}</span>
            <span className="rounded-full bg-[var(--bg-elevated)] px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-[var(--text-muted)]">
              {group.project ?? "company"}
            </span>
            <span className="font-mono text-xs text-[var(--text-muted)]">{duration}</span>
          </div>
          <p className="mt-1 text-sm leading-6 text-[var(--text-primary)]">
            {status} on &quot;{phrase}&quot;
          </p>
        </div>
        <span className="mt-1 rounded border border-[var(--line)] px-2 py-0.5 font-mono text-xs text-[var(--text-muted)]">
          {open ? "hide" : `${group.events.length} events`}
        </span>
      </button>
      {open && (
        <ul className="divide-y divide-[var(--line)] bg-[var(--bg-elevated)]/30">
          {group.events.map((event) => (
            <ChatLine key={event.id} event={event} compact />
          ))}
        </ul>
      )}
    </li>
  );
}

function useTick(ms: number): number {
  const [now, setNow] = useState(0);
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), ms);
    return () => window.clearInterval(id);
  }, [ms]);
  return now;
}
