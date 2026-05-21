"use client";

import { useQuery } from "@tanstack/react-query";
import { type AgentMemory, type Task } from "@/lib/schemas";
import { prettyRole } from "@/lib/roles";

type MemoryResponse = { memory: AgentMemory[] };

async function fetchMemory(agentId: string | null): Promise<MemoryResponse> {
  // Unassigned Tasks have no owner; skip the fetch and return an empty
  // memory block so the drawer still renders cleanly.
  if (!agentId) return { memory: [] };
  const r = await fetch(`/api/agent-memory/${encodeURIComponent(agentId)}`, {
    cache: "no-store",
  });
  if (!r.ok) throw new Error("agent memory fetch failed");
  return r.json();
}

export function TaskDrawer({
  task,
  discussion = [],
  onClose,
}: {
  task: Task;
  // Meeting minutes from the parent Decision's structured_plan
  // (Phase A of enriched-sprint-planning). Empty for legacy / fallback
  // plans; renders a collapsible "Planning discussion" section when
  // populated.
  discussion?: string[];
  onClose: () => void;
}) {
  const memory = useQuery({
    queryKey: ["task-owner-memory", task.owner_agent_id],
    queryFn: () => fetchMemory(task.owner_agent_id),
    initialData: { memory: [] },
  });
  return (
    <div
      className="fixed inset-0 z-50 flex justify-end bg-slate-950/25 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      onClick={onClose}
    >
      <aside
        className="h-full w-full max-w-xl overflow-y-auto border-l border-[var(--line)] bg-[var(--bg-elevated)] p-5 shadow-2xl"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="flex items-start justify-between gap-4">
          <div>
            <div className="text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)]">
              {task.project} · Sprint {task.sprint_number ?? "?"} · {task.category.replace("_", " ")}
            </div>
            <h2 className="mt-1 text-xl font-semibold tracking-tight text-[var(--text-primary)]">
              {task.title}
            </h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md border border-[var(--line)] px-2 py-1 text-sm text-[var(--text-muted)] hover:border-[var(--accent)] hover:text-[var(--text-primary)]"
          >
            Close
          </button>
        </header>

        <div className="mt-4 flex flex-wrap gap-2 text-xs">
          <Pill label={task.status} />
          <Pill label={`effort ${task.estimated_effort}`} />
          <Pill label={prettyRole(task.owner_role)} />
        </div>

        <section
          className={`mt-5 rounded-lg border p-4 ${
            task.status === "unassigned"
              ? "border-amber-300 bg-amber-50/60"
              : "border-[var(--line)] bg-[var(--bg-surface)]"
          }`}
        >
          <div className="text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)]">
            Owner
          </div>
          {task.status === "unassigned" ? (
            <>
              <div className="mt-2 text-sm font-medium text-amber-900">
                ⏳ No owner yet — sitting in backlog
              </div>
              <div className="mt-1 text-xs text-amber-800/80">
                Every eligible{" "}
                <span className="font-mono">{task.owner_role}</span> is at
                their WIP cap. The backlog-sweep cron (every 10 min) will
                assign this task as soon as a slot opens.
              </div>
            </>
          ) : (
            <>
              <div className="mt-2 text-sm font-medium text-[var(--text-primary)]">
                {task.owner_display_name ?? prettyRole(task.owner_role)}
              </div>
              <div className="text-xs text-[var(--text-muted)]">
                {task.owner_agent_id}
              </div>
              {memory.data.memory.length > 0 && (
                <ul className="mt-3 space-y-1.5">
                  {memory.data.memory.slice(0, 3).map((record) => (
                    <li key={record.id} className="rounded bg-white/70 px-2 py-1 text-xs leading-5">
                      {record.summary}
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </section>

        {discussion.length > 0 && (
          <details className="mt-4 rounded-lg border border-[var(--line)] bg-[var(--bg-surface)] p-4">
            <summary className="cursor-pointer text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)]">
              Planning discussion ({discussion.length})
            </summary>
            <ul className="mt-3 space-y-1.5">
              {discussion.map((line, i) => (
                <li
                  key={i}
                  className="rounded bg-white/70 px-2 py-1.5 text-xs leading-5 text-[var(--text-primary)]"
                >
                  {line}
                </li>
              ))}
            </ul>
          </details>
        )}

        <section className="mt-4 rounded-lg border border-[var(--line)] bg-[var(--bg-surface)] p-4">
          <div className="text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)]">
            Description
          </div>
          <p className="mt-2 whitespace-pre-wrap text-sm leading-6">{task.description}</p>
        </section>

        <section className="mt-4 rounded-lg border border-[var(--line)] bg-[var(--bg-surface)] p-4">
          <div className="text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)]">
            Acceptance
          </div>
          <p className="mt-2 whitespace-pre-wrap text-sm leading-6">
            {task.acceptance_criteria || "No acceptance criteria recorded."}
          </p>
        </section>

        {task.pr_url && (
          <a
            href={task.pr_url}
            target="_blank"
            rel="noreferrer"
            className="mt-4 inline-flex rounded-md border border-[var(--accent)]/40 bg-sky-50 px-3 py-2 text-sm font-medium text-[var(--accent)] hover:bg-sky-100"
          >
            Open PR #{task.pr_number ?? ""}
          </a>
        )}
      </aside>
    </div>
  );
}

function Pill({ label }: { label: string }) {
  return (
    <span className="rounded-full border border-[var(--line)] bg-white px-2 py-1 font-medium uppercase tracking-wider text-[var(--text-muted)]">
      {label}
    </span>
  );
}
