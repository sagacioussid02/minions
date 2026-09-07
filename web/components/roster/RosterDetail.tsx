"use client";

import { type AgentProfile, type AgentState, type Task } from "@/lib/schemas";
import { roleShortLabel } from "@/lib/roles";

const TIER_BADGE: Record<string, string> = {
  executive: "border-amber-400/40 text-amber-200",
  engineering: "border-cyan-400/40 text-cyan-200",
  audit: "border-fuchsia-400/40 text-fuchsia-200",
  specialist: "border-emerald-400/40 text-emerald-200",
};

const STATUS_BADGE: Record<string, string> = {
  queued: "border-zinc-500/40 text-zinc-300",
  in_progress: "border-cyan-400/50 text-cyan-200",
  review: "border-amber-400/50 text-amber-200",
  blocked: "border-rose-500/50 text-rose-300",
  done: "border-emerald-500/40 text-emerald-300",
  cancelled: "border-zinc-600/40 text-zinc-400",
};

export function RosterDetail({
  agent,
  tasks,
  profile,
}: {
  agent: AgentState;
  tasks: Task[];
  profile?: AgentProfile | null;
}) {
  const inFlightTasks = tasks.filter(
    (t) => t.status === "in_progress" || t.status === "review"
  );
  const queuedTasks = tasks.filter((t) => t.status === "queued");
  const blockedTasks = tasks.filter((t) => t.status === "blocked");
  const doneTasks = tasks.filter((t) => t.status === "done").slice(0, 5);

  return (
    <div className="space-y-6">
      {/* Header card */}
      <section className="rounded-lg border border-[var(--line)] bg-[var(--surface-1)] p-5">
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="text-2xl font-semibold text-[var(--text-primary)]">
              {agent.display_name?.trim() || roleShortLabel(agent.role)}
            </div>
            <div className="mt-1 text-xs uppercase tracking-wider text-[var(--text-muted)]">
              {roleShortLabel(agent.role)} ·{" "}
              {agent.project ?? "portfolio"}
            </div>
            <div className="mt-2 font-mono text-[10px] text-[var(--text-muted)]">
              {agent.id}
            </div>
          </div>
          <div className="flex flex-col items-end gap-2">
            <span
              className={`rounded border px-2 py-0.5 text-[10px] uppercase tracking-wider ${TIER_BADGE[agent.role_tier] ?? ""}`}
            >
              {agent.role_tier}
            </span>
            <span className="rounded border border-[var(--line)] px-2 py-0.5 text-[10px] text-[var(--text-muted)]">
              {agent.tier}
            </span>
            {agent.in_flight ? (
              <span className="flex items-center gap-1 text-xs text-emerald-300">
                <span className="relative inline-flex size-2">
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400/50" />
                  <span className="relative inline-flex size-2 rounded-full bg-emerald-400" />
                </span>
                in-flight
              </span>
            ) : agent.errored ? (
              <span className="text-xs text-rose-300">errored</span>
            ) : (
              <span className="text-xs text-[var(--text-muted)]">idle</span>
            )}
          </div>
        </div>

        {agent.live_run && (
          <div className="mt-4 rounded border border-emerald-500/30 bg-emerald-500/5 p-3 text-xs">
            <div className="mb-1 font-mono text-[10px] uppercase tracking-wider text-emerald-300">
              live · {agent.live_run.crew}
            </div>
            <div className="text-[var(--text-primary)]">
              {agent.live_run.decision_summary ?? "(working on current task)"}
            </div>
            <div className="mt-1 text-[10px] text-[var(--text-muted)]">
              started {new Date(agent.live_run.started_at).toLocaleString()} ·
              run {agent.live_run.run_id.slice(0, 8)}
            </div>
          </div>
        )}

        <div className="mt-4 grid grid-cols-2 gap-3 text-xs sm:grid-cols-4">
          <Stat label="seats" value={agent.seats.toString()} />
          <Stat
            label="cost today"
            value={`$${agent.cost_today_usd.toFixed(2)}`}
          />
          <Stat
            label="in-flight tasks"
            value={inFlightTasks.length.toString()}
          />
          <Stat label="queued tasks" value={queuedTasks.length.toString()} />
        </div>
      </section>

      {/* Career / dossier — durable identity + lifetime track record */}
      {profile && (
        <section className="rounded-lg border border-[var(--line)] bg-[var(--surface-1)] p-5">
          <h2 className="mb-3 font-mono text-sm uppercase tracking-wider text-[var(--text-muted)]">
            career
          </h2>
          {profile.persona && (
            <p className="mb-4 text-sm leading-relaxed text-[var(--text-primary)]">
              {profile.persona}
            </p>
          )}
          <div className="grid grid-cols-2 gap-3 text-xs sm:grid-cols-5">
            <Stat
              label="joined"
              value={
                profile.joined_sprint != null
                  ? `sprint ${profile.joined_sprint}`
                  : "—"
              }
            />
            <Stat label="PRs opened" value={profile.stats.prs_opened.toString()} />
            <Stat label="PRs merged" value={profile.stats.prs_merged.toString()} />
            <Stat label="reviews" value={profile.stats.reviews_received.toString()} />
            <Stat label="blockers" value={profile.stats.blockers_hit.toString()} />
          </div>
          {profile.specialties.length > 0 && (
            <div className="mt-4">
              <div className="mb-1.5 text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
                specialties
              </div>
              <div className="flex flex-wrap gap-1.5">
                {profile.specialties.map((s) => (
                  <span
                    key={s}
                    className="rounded border border-[var(--line)] bg-[var(--surface-2)] px-2 py-0.5 text-[11px] text-[var(--text-primary)]"
                  >
                    {s}
                  </span>
                ))}
              </div>
            </div>
          )}
          {profile.stats.last_active_at && (
            <div className="mt-4 text-[10px] text-[var(--text-muted)]">
              last active {new Date(profile.stats.last_active_at).toLocaleString()}
            </div>
          )}
        </section>
      )}

      {/* Tasks */}
      <section className="rounded-lg border border-[var(--line)] bg-[var(--surface-1)] p-5">
        <h2 className="mb-3 font-mono text-sm uppercase tracking-wider text-[var(--text-muted)]">
          assigned tasks ({tasks.length})
        </h2>
        {tasks.length === 0 ? (
          <div className="rounded border border-dashed border-[var(--line)] p-4 text-center text-xs text-[var(--text-muted)]">
            No tasks assigned right now.
          </div>
        ) : (
          <div className="space-y-4">
            {inFlightTasks.length > 0 && (
              <TaskGroup
                heading="now working on"
                emphasis
                tasks={inFlightTasks}
              />
            )}
            {queuedTasks.length > 0 && (
              <TaskGroup heading="queued next" tasks={queuedTasks} />
            )}
            {blockedTasks.length > 0 && (
              <TaskGroup heading="blocked" tasks={blockedTasks} />
            )}
            {doneTasks.length > 0 && (
              <TaskGroup
                heading={`recently done (${doneTasks.length}/${tasks.filter((t) => t.status === "done").length})`}
                tasks={doneTasks}
                dim
              />
            )}
          </div>
        )}
      </section>

      {/* Recent activity */}
      <section className="rounded-lg border border-[var(--line)] bg-[var(--surface-1)] p-5">
        <h2 className="mb-3 font-mono text-sm uppercase tracking-wider text-[var(--text-muted)]">
          recent activity
        </h2>
        {agent.recent_events.length === 0 ? (
          <div className="text-xs text-[var(--text-muted)]">
            No recorded activity yet.
          </div>
        ) : (
          <ul className="space-y-2">
            {agent.recent_events.slice(0, 12).map((e, i) => (
              <li
                key={`${e.ts}-${i}`}
                className="flex items-start gap-3 text-xs"
              >
                <span className="shrink-0 font-mono text-[10px] text-[var(--text-muted)]">
                  {new Date(e.ts).toLocaleTimeString([], {
                    hour: "2-digit",
                    minute: "2-digit",
                  })}
                </span>
                <span className="text-[var(--text-primary)]">
                  {e.sentence}
                  {e.pr_url && (
                    <a
                      href={e.pr_url}
                      target="_blank"
                      rel="noreferrer"
                      className="ml-2 text-[var(--accent)] hover:underline"
                    >
                      pr ↗
                    </a>
                  )}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded border border-[var(--line)] bg-[var(--surface-2)] p-2">
      <div className="text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
        {label}
      </div>
      <div className="mt-0.5 font-mono text-lg text-[var(--text-primary)]">
        {value}
      </div>
    </div>
  );
}

function TaskGroup({
  heading,
  tasks,
  emphasis = false,
  dim = false,
}: {
  heading: string;
  tasks: Task[];
  emphasis?: boolean;
  dim?: boolean;
}) {
  return (
    <div>
      <div
        className={`mb-2 text-[10px] uppercase tracking-wider ${emphasis ? "text-emerald-300" : "text-[var(--text-muted)]"}`}
      >
        {heading}
      </div>
      <ul className="space-y-1.5">
        {tasks.map((t) => (
          <li
            key={t.id}
            className={`flex items-start gap-3 rounded border border-[var(--line)] bg-[var(--surface-2)] p-2 text-xs ${dim ? "opacity-60" : ""}`}
          >
            <span
              className={`shrink-0 rounded border px-1.5 py-0.5 text-[9px] uppercase tracking-wider ${STATUS_BADGE[t.status] ?? "border-[var(--line)] text-[var(--text-muted)]"}`}
            >
              {t.status.replace("_", " ")}
            </span>
            <div className="min-w-0 flex-1">
              <div className="truncate text-[var(--text-primary)]">
                {t.title}
              </div>
              {t.acceptance_criteria && (
                <div className="mt-0.5 truncate text-[10px] text-[var(--text-muted)]">
                  {t.acceptance_criteria.slice(0, 140)}
                </div>
              )}
            </div>
            {t.pr_url && (
              <a
                href={t.pr_url}
                target="_blank"
                rel="noreferrer"
                className="shrink-0 text-[10px] text-[var(--accent)] hover:underline"
              >
                pr ↗
              </a>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
