"use client";

import { useEffect, useMemo, useState } from "react";
import { Avatar } from "@/components/Avatar";
import { describe } from "@/lib/activity-renderer";
import { type ActivityEvent } from "@/lib/schemas";
import { prettyRole, tierFor } from "@/lib/roles";
import { format } from "date-fns";

type DemoRunResponse = {
  mode: "safe-demo";
  writes_github: false;
  events: ActivityEvent[];
};

type DemoMode = "off" | "replay" | "safe-demo";

export function InvestorDemoMode({ initialEvents }: { initialEvents: ActivityEvent[] }) {
  const replayEvents = useMemo(
    () =>
      initialEvents
        .filter((event) => event.role || event.crew)
        .slice(0, 18)
        .reverse(),
    [initialEvents],
  );
  const [mode, setMode] = useState<DemoMode>("off");
  const [events, setEvents] = useState<ActivityEvent[]>(replayEvents);
  const [index, setIndex] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const active = mode === "off" ? null : events[index] ?? null;

  useEffect(() => {
    if (mode === "off" || events.length === 0) return;
    const event = events[index] ?? events[0];
    window.dispatchEvent(
      new CustomEvent("minions:spotlight", {
        detail: {
          agentId: agentIdFor(event),
          event,
        },
      }),
    );
    const id = window.setTimeout(() => {
      setIndex((current) => (current + 1) % events.length);
    }, mode === "safe-demo" ? 1400 : 2200);
    return () => window.clearTimeout(id);
  }, [events, index, mode]);

  async function startReplay() {
    setError(null);
    setEvents(replayEvents);
    setIndex(0);
    setMode("replay");
  }

  async function startSafeDemo() {
    setBusy(true);
    setError(null);
    try {
      const response = await fetch("/api/demo-run", { method: "POST" });
      if (!response.ok) throw new Error("demo run failed");
      const payload = (await response.json()) as DemoRunResponse;
      setEvents(payload.events);
      setIndex(0);
      setMode("safe-demo");
    } catch (err) {
      setError(err instanceof Error ? err.message : "demo run failed");
    } finally {
      setBusy(false);
    }
  }

  function stop() {
    setMode("off");
    window.dispatchEvent(new CustomEvent("minions:spotlight", { detail: { agentId: null } }));
  }

  const role = active?.role ?? active?.crew ?? "system";
  const tier = tierFor(role);
  const tierColor = `var(--color-role-${tier})`;

  return (
    <section className="overflow-hidden rounded-2xl border border-[var(--line)] bg-[var(--bg-elevated)] shadow-sm">
      <div className="flex flex-col gap-3 border-b border-[var(--line)] px-5 py-4 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <div className="flex items-center gap-2">
            <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-emerald-700">
              Investor Demo Mode
            </span>
            {mode !== "off" && (
              <span className="rounded-full bg-sky-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-sky-700">
                {mode === "safe-demo" ? "safe simulated run" : "real event replay"}
              </span>
            )}
          </div>
          <h2 className="mt-2 text-lg font-semibold tracking-tight">
            Watch the crew move through work
          </h2>
          <p className="mt-1 text-sm text-[var(--text-muted)]">
            Replays real activity and highlights agents. Safe demo run does not write to GitHub.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            className="rounded-lg border border-[var(--line)] bg-white px-3 py-2 text-sm font-medium shadow-sm transition hover:border-[var(--accent)] hover:text-[var(--accent)]"
            onClick={startReplay}
            disabled={replayEvents.length === 0}
          >
            Replay real activity
          </button>
          <button
            type="button"
            className="rounded-lg bg-[var(--accent)] px-3 py-2 text-sm font-semibold text-white shadow-sm transition hover:brightness-105 disabled:opacity-60"
            onClick={startSafeDemo}
            disabled={busy}
          >
            {busy ? "Starting..." : "Trigger safe demo run"}
          </button>
          {mode !== "off" && (
            <button
              type="button"
              className="rounded-lg border border-[var(--line)] px-3 py-2 text-sm text-[var(--text-muted)] transition hover:border-[var(--accent)] hover:text-[var(--text-primary)]"
              onClick={stop}
            >
              Stop
            </button>
          )}
        </div>
      </div>

      <div className="grid gap-4 px-5 py-4 xl:grid-cols-[minmax(0,1fr)_420px]">
        <div className="flex min-h-[112px] items-center gap-4 rounded-xl border border-[var(--line)] bg-[var(--bg-surface)] px-4 py-3">
          {active ? (
            <>
              <Avatar seed={agentIdFor(active)} size={64} ring={tierColor} mood="active" />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2 text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
                  <span style={{ color: tierColor }}>{prettyRole(role)}</span>
                  {active.project && <span>{active.project}</span>}
                  <span>{format(new Date(active.ts), "HH:mm:ss")}</span>
                </div>
                <div className="mt-1 text-base font-semibold tracking-tight">
                  {describe(active)}
                </div>
                <div className="mt-1 text-xs text-[var(--text-muted)]">
                  {mode === "safe-demo"
                    ? "Simulated for presentation only. GitHub writes are disabled."
                    : "Replay of a real event already recorded by the crew."}
                </div>
              </div>
            </>
          ) : (
            <div className="text-sm text-[var(--text-muted)]">
              Start replay or safe demo to spotlight the crew.
            </div>
          )}
        </div>

        <ol className="max-h-[180px] space-y-2 overflow-y-auto pr-1">
          {(mode === "off" ? replayEvents.slice(0, 5) : events).map((event, eventIndex) => {
            const selected = mode !== "off" && eventIndex === index;
            const eventRole = event.role ?? event.crew ?? "system";
            const eventTierColor = `var(--color-role-${tierFor(eventRole)})`;
            return (
              <li
                key={`${event.id}-${eventIndex}`}
                className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-sm transition ${
                  selected
                    ? "border-[var(--accent)] bg-sky-50 shadow-sm"
                    : "border-[var(--line)] bg-white/70"
                }`}
              >
                <span
                  className="size-2 shrink-0 rounded-full"
                  style={{ backgroundColor: eventTierColor }}
                  aria-hidden
                />
                <span className="min-w-0 flex-1 truncate">{describe(event)}</span>
              </li>
            );
          })}
        </ol>
      </div>
      {error && (
        <div className="border-t border-[var(--line)] px-5 py-2 text-xs text-red-600">
          {error}
        </div>
      )}
    </section>
  );
}

function agentIdFor(event: ActivityEvent): string {
  const role = event.role ?? event.crew ?? "system";
  return `${role}@${event.project ?? "shared"}`;
}
