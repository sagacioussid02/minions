"use client";

import { useMemo, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Avatar } from "@/components/Avatar";
import { TaskDrawer } from "@/components/sprint/TaskDrawer";
import {
  type SprintBoard as Board,
  type SprintCard,
  type SprintColumn,
  type SprintReviewer,
  type SprintWindow,
  type Task,
  type PlanItem,
} from "@/lib/schemas";
import { prettyRole } from "@/lib/roles";
import { colorFor, registerProjects } from "@/lib/project-color";
import { CRON_SCHEDULES, describeNextRun } from "@/lib/cron-schedule";

// Backlog is intentionally absent from the rendered order — the column
// still exists in the schema for compatibility, but it's unused in
// today's flow and hiding it removes a permanently empty lane from the
// operator's view.
const COLUMN_ORDER: SprintColumn[] = [
  "awaiting_you",
  "approved",
  "in_progress",
  "review",
  "done",
];

const COLUMN_LABEL: Record<SprintColumn, string> = {
  backlog: "Backlog",
  awaiting_you: "Awaiting You",
  approved: "Approved",
  in_progress: "In Progress",
  review: "Review",
  done: "Done",
};

const COLUMN_HINT: Record<SprintColumn, string> = {
  backlog: "Ideas drafted, not yet up for approval.",
  awaiting_you: "Pending your decision.",
  approved: "Approved · engineer crew queued.",
  in_progress: "Engineer crew at work or CI running.",
  review: "PR open · CI green · waiting on merge.",
  done: "Shipped.",
};

const WINDOW_OPTIONS: Array<{ value: SprintWindow; label: string }> = [
  { value: "this_week", label: "This week" },
  { value: "last_week", label: "Last week" },
  { value: "last_30d", label: "30d" },
  { value: "last_90d", label: "90d" },
  { value: "all", label: "All" },
];

async function fetchBoard(project: string | null, window: SprintWindow): Promise<Board> {
  const params = new URLSearchParams({ window });
  if (project) params.set("project", project);
  const url = `/api/sprint-board?${params.toString()}`;
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) throw new Error("sprint board fetch failed");
  return r.json();
}

function primaryOwner(card: SprintCard): string | null {
  // Most common task owner on this card — what the operator scans for.
  const counts = new Map<string, number>();
  for (const t of card.tasks) {
    const name = t.owner_display_name;
    if (!name) continue;
    counts.set(name, (counts.get(name) ?? 0) + 1);
  }
  let best: string | null = null;
  let bestCount = 0;
  for (const [name, n] of counts) {
    if (n > bestCount) {
      best = name;
      bestCount = n;
    }
  }
  return best;
}

const CLOSED_REVIEW_STATES = new Set(["closed", "superseded"]);

export function SprintBoard({ initial }: { initial: Board }) {
  const [tab, setTab] = useState<string | null>(null); // null = All
  const [window, setWindow] = useState<SprintWindow>("this_week");
  const [ownerFilter, setOwnerFilter] = useState<string | null>(null);
  const [sprintFilter, setSprintFilter] = useState<number | null>(null);
  const [showClosed, setShowClosed] = useState(false);
  const [selectedTask, setSelectedTask] = useState<Task | null>(null);

  const { data } = useQuery({
    queryKey: ["sprint-board", tab, window],
    queryFn: () => fetchBoard(tab, window),
    initialData: tab === null && window === "this_week" ? initial : undefined,
    refetchInterval: 5_000,
  });

  const board = data ?? initial;

  // Stable palette assignments.
  registerProjects(board.projects);

  const ownerOptions = useMemo(() => {
    const set = new Set<string>();
    for (const c of board.cards) {
      for (const t of c.tasks) {
        if (t.owner_display_name) set.add(t.owner_display_name);
      }
    }
    return [...set].sort((a, b) => a.localeCompare(b));
  }, [board.cards]);

  const sprintOptions = useMemo(() => {
    const set = new Set<number>();
    for (const c of board.cards) {
      if (c.sprint_number !== null) set.add(c.sprint_number);
    }
    return [...set].sort((a, b) => b - a);
  }, [board.cards]);

  const filteredCards = useMemo(() => {
    return board.cards.filter((c) => {
      if (!showClosed && CLOSED_REVIEW_STATES.has(c.review_status)) return false;
      if (sprintFilter !== null && c.sprint_number !== sprintFilter) return false;
      if (ownerFilter !== null) {
        const matches = c.tasks.some((t) => t.owner_display_name === ownerFilter);
        if (!matches) return false;
      }
      return true;
    });
  }, [board.cards, showClosed, sprintFilter, ownerFilter]);

  const byColumn = useMemo(() => {
    const m = new Map<SprintColumn, SprintCard[]>();
    for (const c of filteredCards) {
      const arr = m.get(c.column) ?? [];
      arr.push(c);
      m.set(c.column, arr);
    }
    return m;
  }, [filteredCards]);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-2 xl:flex-row xl:items-center xl:justify-between">
        <Tabs current={tab} projects={board.projects} onChange={setTab} />
        <WindowFilter current={window} onChange={setWindow} />
      </div>
      <FilterRow
        ownerOptions={ownerOptions}
        owner={ownerFilter}
        onOwnerChange={setOwnerFilter}
        sprintOptions={sprintOptions}
        sprint={sprintFilter}
        onSprintChange={setSprintFilter}
        showClosed={showClosed}
        onShowClosedChange={setShowClosed}
      />
      <SprintHeaderStrip cards={filteredCards} />
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-3 xl:grid-cols-5">
        {COLUMN_ORDER.map((col) => (
          <Column
            key={col}
            column={col}
            cards={byColumn.get(col) ?? []}
            onTaskSelect={setSelectedTask}
          />
        ))}
      </div>
      <JargonLegend />
      {selectedTask && (
        <TaskDrawer
          task={selectedTask}
          discussion={
            board.cards.find((card) => card.decision_id === selectedTask.decision_id)
              ?.structured_plan?.discussion ?? []
          }
          onClose={() => setSelectedTask(null)}
        />
      )}
    </div>
  );
}

function FilterRow({
  ownerOptions,
  owner,
  onOwnerChange,
  sprintOptions,
  sprint,
  onSprintChange,
  showClosed,
  onShowClosedChange,
}: {
  ownerOptions: string[];
  owner: string | null;
  onOwnerChange: (v: string | null) => void;
  sprintOptions: number[];
  sprint: number | null;
  onSprintChange: (v: number | null) => void;
  showClosed: boolean;
  onShowClosedChange: (v: boolean) => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs">
      <label className="flex items-center gap-1.5 rounded-xl border border-[var(--line)] bg-[var(--bg-surface)] px-2 py-1.5">
        <span className="text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
          Agent
        </span>
        <select
          value={owner ?? ""}
          onChange={(e) => onOwnerChange(e.target.value || null)}
          className="bg-transparent text-xs text-[var(--text-primary)] outline-none"
        >
          <option value="">All</option>
          {ownerOptions.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
      </label>
      <label className="flex items-center gap-1.5 rounded-xl border border-[var(--line)] bg-[var(--bg-surface)] px-2 py-1.5">
        <span className="text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
          Sprint
        </span>
        <select
          value={sprint ?? ""}
          onChange={(e) => onSprintChange(e.target.value ? Number(e.target.value) : null)}
          className="bg-transparent text-xs text-[var(--text-primary)] outline-none"
        >
          <option value="">All</option>
          {sprintOptions.map((n) => (
            <option key={n} value={n}>
              Sprint {n}
            </option>
          ))}
        </select>
      </label>
      <label className="flex items-center gap-1.5 rounded-xl border border-[var(--line)] bg-[var(--bg-surface)] px-2 py-1.5 text-[var(--text-muted)]">
        <input
          type="checkbox"
          checked={showClosed}
          onChange={(e) => onShowClosedChange(e.target.checked)}
        />
        <span>Show closed/superseded</span>
      </label>
    </div>
  );
}

function WindowFilter({
  current,
  onChange,
}: {
  current: SprintWindow;
  onChange: (w: SprintWindow) => void;
}) {
  return (
    <div className="flex shrink-0 items-center gap-1 overflow-x-auto rounded-xl border border-[var(--line)] bg-[var(--bg-surface)] p-1.5">
      {WINDOW_OPTIONS.map((option) => (
        <button
          key={option.value}
          type="button"
          onClick={() => onChange(option.value)}
          className={`rounded-lg px-3 py-1.5 text-xs transition-colors ${
            current === option.value
              ? "bg-[var(--bg-elevated)] text-[var(--text-primary)]"
              : "text-[var(--text-muted)] hover:text-[var(--text-primary)]"
          }`}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

function Tabs({
  current,
  projects,
  onChange,
}: {
  current: string | null;
  projects: string[];
  onChange: (p: string | null) => void;
}) {
  return (
    <div className="flex items-center gap-1 overflow-x-auto rounded-xl border border-[var(--line)] bg-[var(--bg-surface)] p-1.5">
      <TabButton label="All" active={current === null} color="var(--text-muted)" onClick={() => onChange(null)} />
      {projects.map((p) => (
        <TabButton
          key={p}
          label={p}
          active={current === p}
          color={colorFor(p)}
          onClick={() => onChange(p)}
        />
      ))}
    </div>
  );
}

function TabButton({
  label,
  active,
  color,
  onClick,
}: {
  label: string;
  active: boolean;
  color: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`relative flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs transition-colors ${
        active
          ? "bg-[var(--bg-elevated)] text-[var(--text-primary)]"
          : "text-[var(--text-muted)] hover:text-[var(--text-primary)]"
      }`}
    >
      <span className="inline-block size-1.5 rounded-full" style={{ background: color }} />
      {label}
    </button>
  );
}

function SprintHeaderStrip({ cards }: { cards: SprintCard[] }) {
  const byProject = useMemo(() => {
    const map = new Map<string, SprintCard[]>();
    for (const card of cards) {
      const arr = map.get(card.project) ?? [];
      arr.push(card);
      map.set(card.project, arr);
    }
    return [...map.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [cards]);
  if (byProject.length === 0) return null;
  return (
    <div className="grid grid-cols-1 gap-2 xl:grid-cols-3">
      {byProject.map(([project, projectCards]) => {
        const sprint = projectCards
          .map((card) => card.sprint_number)
          .filter((value): value is number => value !== null)
          .sort((a, b) => b - a)[0];
        const tasks = projectCards.flatMap((card) => card.tasks);
        const unassigned = tasks.filter((task) => task.status === "unassigned").length;
        const queued = tasks.filter((task) => task.status === "queued").length;
        const review = tasks.filter((task) => task.status === "review").length;
        const blocked = tasks.filter((task) => task.status === "blocked").length;
        const goal = projectCards.find((card) => card.structured_plan)?.structured_plan?.goal;
        return (
          <div key={project} className="rounded-xl border border-[var(--line)] bg-[var(--bg-surface)] p-3">
            <div className="flex items-center gap-2">
              <span className="size-2 rounded-full" style={{ backgroundColor: colorFor(project) }} />
              <span className="font-semibold text-[var(--text-primary)]">{project}</span>
              <span className="ml-auto text-xs text-[var(--text-muted)]">
                Sprint {sprint ?? "?"}
              </span>
            </div>
            <div className="mt-1 line-clamp-2 text-xs text-[var(--text-muted)]">
              {goal ?? "No structured sprint goal recorded yet."}
            </div>
            <div className="mt-2 flex flex-wrap gap-1.5 text-[10px]">
              <Pill label={`${tasks.length} tasks`} tone="muted" />
              {unassigned > 0 && (
                <Pill label={`${unassigned} in backlog`} tone="warn" />
              )}
              <Pill label={`${queued} queued`} tone="muted" />
              <Pill label={`${review} review`} tone="success" />
              {blocked > 0 && <Pill label={`${blocked} blocked`} tone="danger" />}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function Column({
  column,
  cards,
  onTaskSelect,
}: {
  column: SprintColumn;
  cards: SprintCard[];
  onTaskSelect: (task: Task) => void;
}) {
  const stalled = cards.filter((c) => c.stalled).length;
  return (
    <div className="rounded-xl border border-[var(--line)] bg-[var(--bg-surface)] p-3">
      <header className="mb-2 flex items-center gap-2">
        <h3 className="text-xs font-medium uppercase tracking-wider text-[var(--text-primary)]">
          {COLUMN_LABEL[column]}
        </h3>
        <span className="text-[10px] text-[var(--text-muted)]">{cards.length}</span>
        {stalled > 0 && (
          <span className="ml-auto rounded bg-[var(--state-warn)]/15 px-1 text-[9px] uppercase tracking-wider text-[var(--state-warn)]">
            {stalled} stalled
          </span>
        )}
      </header>
      <p className="mb-3 text-[10px] text-[var(--text-muted)]">{COLUMN_HINT[column]}</p>
      <ul className="flex flex-col gap-2">
        {cards.length === 0 ? (
          <li className="rounded-md border border-dashed border-[var(--line)] p-3 text-center text-[10px] text-[var(--text-muted)]">
            empty
          </li>
        ) : (
          cards.map((c) => <Card key={c.decision_id} card={c} onTaskSelect={onTaskSelect} />)
        )}
      </ul>
    </div>
  );
}

function Card({ card, onTaskSelect }: { card: SprintCard; onTaskSelect: (task: Task) => void }) {
  const qc = useQueryClient();
  const projectColor = colorFor(card.project);
  const ageLabel = formatAgeMinutes(card.age_minutes);

  const invalidate = () => qc.invalidateQueries({ queryKey: ["sprint-board"] });

  const approve = useMutation({
    mutationFn: async () => {
      const r = await fetch(`/api/decisions/${card.decision_id}/approve`, { method: "POST" });
      if (!r.ok) throw new Error(`approve failed (${r.status})`);
    },
    onSuccess: invalidate,
  });
  const reject = useMutation({
    mutationFn: async (reason: string) => {
      const r = await fetch(`/api/decisions/${card.decision_id}/reject`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason }),
      });
      if (!r.ok) throw new Error(`reject failed (${r.status})`);
    },
    onSuccess: invalidate,
  });
  const merge = useMutation({
    mutationFn: async () => {
      const r = await fetch(`/api/work-items/${card.decision_id}/merge`, { method: "POST" });
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        throw new Error(body.reason ?? body.error ?? `merge failed (${r.status})`);
      }
    },
    onSuccess: invalidate,
  });

  const busy = approve.isPending || reject.isPending || merge.isPending;
  const errMsg = approve.error?.message ?? reject.error?.message ?? merge.error?.message;
  const owner = primaryOwner(card);

  return (
    <li
      className={`row-in relative rounded-lg border border-[var(--line)] bg-[var(--bg-elevated)] p-3 transition-shadow hover:border-[var(--accent)]/40 ${
        card.stalled ? "ring-1 ring-[var(--state-warn)]/50" : ""
      }`}
    >
      <span
        className="pointer-events-none absolute inset-y-2 left-0 w-0.5 rounded-full"
        style={{ background: projectColor, opacity: 0.85 }}
      />
      <div className="flex items-start gap-2">
        <Avatar seed={card.avatar_seed} size={28} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5 text-[10px] text-[var(--text-muted)]">
            <span>{card.project}</span>
            <span>·</span>
            <span>{card.proposer_display_name ?? prettyRole(card.proposer_role ?? "system")}</span>
            {owner && (
              <span
                className="rounded bg-[var(--bg-surface)] px-1.5 py-0.5 text-[9px] font-medium uppercase tracking-wider text-[var(--text-primary)]"
                title={`Owner: ${owner}`}
              >
                👤 {owner}
              </span>
            )}
            <PriorityPill priority={card.priority} expedited={card.expedited} />
            <RiskPill risk={card.risk} />
            {card.has_devils_advocate && <Pill label="DA" tone="audit" />}
            {card.has_security_review && <Pill label="SEC" tone="audit" />}
            {card.ci_conclusion && card.ci_conclusion !== "success" && (
              <Pill label={`CI ${card.ci_conclusion}`} tone={card.ci_conclusion === "failure" ? "danger" : "muted"} />
            )}
            <Pill label={card.review_status_label ?? "Not in review"} tone={toneForReview(card.review_status)} />
          </div>
          <div className="mt-0.5 text-xs font-medium text-[var(--text-primary)]" title={card.summary}>
            {truncate(card.summary, 90)}
          </div>
          {card.structured_plan?.goal && (
            <div
              className="mt-1 line-clamp-2 rounded-md border-l-2 border-[var(--accent)]/60 bg-[var(--bg-surface)]/60 px-2 py-1 text-[11px] font-medium leading-snug text-[var(--text-primary)]"
              title={card.structured_plan.goal}
            >
              🎯 {card.structured_plan.goal}
            </div>
          )}
          {card.sprint_number !== null && (
            <div className="mt-1 text-[10px] font-medium uppercase tracking-wider text-[var(--accent)]">
              Sprint {card.sprint_number}
            </div>
          )}
          <SubtaskProgress tasks={card.tasks} />
          {card.structured_plan && (
            <StructuredPlanMini card={card} onTaskSelect={onTaskSelect} />
          )}
          <div className="mt-1 flex flex-wrap items-center gap-2 text-[10px] text-[var(--text-muted)]">
            <span>{ageLabel}</span>
            <CardPRList card={card} />
          </div>
          {card.live_crew && (
            <div
              className="mt-2 flex items-center gap-1.5 rounded-md border border-emerald-500/40 bg-emerald-500/10 px-2 py-1 text-[10px] text-emerald-200"
              title={`run_id: ${card.live_crew.run_id}`}
            >
              <span className="relative inline-flex h-2 w-2">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400/60" />
                <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-400" />
              </span>
              <span>
                <span className="font-medium">{prettyRole(card.live_crew.crew)} crew</span> running ·{" "}
                {liveDuration(card.live_crew.started_at)}
                {card.live_crew.agents.length > 0 && (
                  <span className="text-emerald-200/70">
                    {" "}· {card.live_crew.agents.map(prettyRole).join(", ")}
                  </span>
                )}
              </span>
            </div>
          )}
          {!card.live_crew && card.column === "approved" && (
            <div
              className={`mt-2 rounded-md border px-2 py-1 text-[10px] ${
                card.expedited
                  ? "border-[var(--state-warn)]/45 bg-[var(--state-warn)]/10 text-[var(--text-primary)]"
                  : "border-[var(--line)] bg-[var(--bg-surface)]/65 text-[var(--text-muted)]"
              }`}
              title={`Regular sweep: ${CRON_SCHEDULES["execute-approved"].expr} UTC. Expedited sweep: ${CRON_SCHEDULES["execute-expedited"].expr} UTC.`}
            >
              {card.expedited ? (
                <>
                  Expedited pickup: {describeNextRun(CRON_SCHEDULES["execute-expedited"].expr)}
                  <span className="text-[var(--text-muted)]/75">
                    {" "}· requested by {prettyRole(card.requested_by_role ?? "leadership")}
                  </span>
                </>
              ) : (
                <>
                  Next engineer pickup: {describeNextRun(CRON_SCHEDULES["execute-approved"].expr)}
                  {" · "}
                  <span className="text-[var(--text-muted)]/70">
                    expedited lane: {describeNextRun(CRON_SCHEDULES["execute-expedited"].expr)}
                  </span>
                </>
              )}
            </div>
          )}
          {(card.pr_url || card.iteration_count > 0 || card.column === "review" || card.column === "in_progress") && (
            <ReviewTrail card={card} />
          )}
          {/* Action row — only renders when there is something to do */}
          {(card.column === "awaiting_you" || card.can_auto_merge) && (
            <div className="mt-2 flex items-center gap-1.5">
              {card.column === "awaiting_you" && (
                <>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => approve.mutate()}
                    className="rounded-md bg-[var(--state-success)]/15 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider text-[var(--state-success)] hover:bg-[var(--state-success)]/25 disabled:opacity-40"
                  >
                    Approve
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => {
                      const reason = prompt("Reason for rejecting?", "operator rejected");
                      if (reason !== null) reject.mutate(reason);
                    }}
                    className="rounded-md bg-[var(--state-danger)]/15 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider text-[var(--state-danger)] hover:bg-[var(--state-danger)]/25 disabled:opacity-40"
                  >
                    Reject
                  </button>
                </>
              )}
              {card.can_auto_merge && (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => merge.mutate()}
                  className="rounded-md bg-[var(--accent)]/15 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider text-[var(--accent)] hover:bg-[var(--accent)]/25 disabled:opacity-40"
                >
                  Auto-merge
                </button>
              )}
            </div>
          )}
          {errMsg && (
            <div className="mt-1 text-[10px] text-[var(--state-danger)]">{errMsg}</div>
          )}
        </div>
      </div>
    </li>
  );
}

function SubtaskProgress({ tasks }: { tasks: Task[] }) {
  const active = tasks.filter((t) => t.status !== "cancelled");
  if (active.length === 0) return null;
  const done = active.filter((t) => t.status === "done").length;
  const pct = Math.round((done / active.length) * 100);
  const allDone = done === active.length;
  return (
    <div className="mt-1.5" title={`${done} of ${active.length} subtasks done`}>
      <div className="mb-0.5 flex items-center justify-between text-[9px] uppercase tracking-wider text-[var(--text-muted)]">
        <span>subtasks</span>
        <span className="tabular-nums">
          {done}/{active.length} done
        </span>
      </div>
      <div className="h-1 overflow-hidden rounded-full bg-[var(--line)]">
        <div
          className="h-full rounded-full"
          style={{
            width: `${pct}%`,
            background: allDone ? "var(--state-success)" : "var(--accent)",
          }}
        />
      </div>
    </div>
  );
}

function ReviewTrail({ card }: { card: SprintCard }) {
  const reviewers = card.reviewers ?? [];
  return (
    <div className="mt-2 rounded-md border border-[var(--line)] bg-[var(--bg-surface)]/65 p-2">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="text-[10px] font-medium uppercase tracking-wider text-[var(--text-muted)]">
            crew loop
          </div>
          <div className="mt-0.5 text-[10px] leading-snug text-[var(--text-primary)]">
            {card.crew_last_action ?? "Waiting for the PR to reach crew review."}
          </div>
        </div>
        {card.iteration_count > 0 && (
          <span
            className="shrink-0 rounded bg-[var(--state-warn)]/15 px-1.5 py-0.5 text-[9px] uppercase tracking-wider text-[var(--state-warn)]"
            title={card.last_failure_kind ?? undefined}
          >
            iter {card.iteration_count}
            {card.last_failure_kind && (
              <span className="ml-1 normal-case opacity-80">
                · {formatFailureKind(card.last_failure_kind)}
              </span>
            )}
          </span>
        )}
      </div>
      <div className="mt-2 grid grid-cols-3 gap-1.5">
        {reviewers.map((reviewer) => (
          <ReviewerChip key={`${card.decision_id}-${reviewer.role}`} reviewer={reviewer} />
        ))}
      </div>
    </div>
  );
}

function CardPRList({ card }: { card: SprintCard }) {
  // Collect every PR linked from this card — both the legacy
  // decision-level PR and any per-task PRs (which is the norm now that
  // refinement assigns one PR per task). Dedupe by pr_number so the
  // legacy + per-task pointers don't double-render.
  const seen = new Set<number>();
  const items: Array<{ url: string; number: number }> = [];
  if (card.pr_url && card.pr_number !== null) {
    seen.add(card.pr_number);
    items.push({ url: card.pr_url, number: card.pr_number });
  }
  for (const t of card.tasks) {
    if (t.pr_url && t.pr_number !== null && !seen.has(t.pr_number)) {
      seen.add(t.pr_number);
      items.push({ url: t.pr_url, number: t.pr_number });
    }
  }
  if (items.length === 0) return null;
  return (
    <>
      {items.map((p) => (
        <a
          key={p.number}
          href={p.url}
          target="_blank"
          rel="noreferrer"
          className="rounded border border-[var(--line)] px-1.5 py-0.5 font-mono hover:border-[var(--accent)]/40 hover:text-[var(--text-primary)]"
        >
          #{p.number}
        </a>
      ))}
    </>
  );
}

function StructuredPlanMini({
  card,
  onTaskSelect,
}: {
  card: SprintCard;
  onTaskSelect: (task: Task) => void;
}) {
  if (!card.structured_plan) return null;
  type SectionKey = "features" | "bugs" | "tech_debt" | "ops" | "docs";
  const sections: Array<[string, SectionKey]> = [
    ["Features", "features"],
    ["Bugs", "bugs"],
    ["Tech debt", "tech_debt"],
    ["Ops", "ops"],
    ["Docs", "docs"],
  ];
  // Plan item title → best-match Task. Exact (case-insensitive) match wins;
  // otherwise the first task whose title contains or is contained by the
  // plan item title (handles refinement prefixes like "[Parent] step 1"
  // and the operator-visible bug where many rows were un-clickable).
  function findTask(planTitle: string): Task | undefined {
    const needle = planTitle.toLowerCase().trim();
    const exact = card.tasks.find((t) => t.title.toLowerCase().trim() === needle);
    if (exact) return exact;
    return card.tasks.find((t) => {
      const hay = t.title.toLowerCase();
      return hay.includes(needle) || needle.includes(hay);
    });
  }
  return (
    <div className="mt-2 rounded-md border border-[var(--line)] bg-[var(--bg-surface)]/70 p-2">
      <div className="line-clamp-2 text-[10px] leading-snug text-[var(--text-muted)]">
        Goal: {card.structured_plan.goal}
      </div>
      <div className="mt-2 space-y-1.5">
        {sections.map(([label, key]) => {
          const items: PlanItem[] = card.structured_plan?.[key] ?? [];
          if (!Array.isArray(items) || items.length === 0) return null;
          return (
            <div key={key}>
              <div className="text-[9px] font-semibold uppercase tracking-wider text-[var(--text-muted)]">
                {label} ({items.length})
              </div>
              <ul className="mt-1 space-y-1">
                {items.slice(0, 3).map((item) => {
                  const task = findTask(item.title);
                  const isUnassigned = task?.status === "unassigned";
                  const ownerLabel = task?.owner_display_name
                    ? task.owner_display_name
                    : isUnassigned
                      ? "no owner — picked up automatically"
                      : null;
                  const subtaskCount = (item.subtasks || []).length;
                  return (
                    <li key={`${key}-${item.title}`}>
                      <button
                        type="button"
                        disabled={!task}
                        onClick={() => task && onTaskSelect(task)}
                        className={`flex w-full items-center gap-1.5 rounded px-2 py-1 text-left text-[10px] text-[var(--text-primary)] disabled:cursor-default disabled:opacity-80 ${
                          isUnassigned
                            ? "border border-dashed border-amber-300 bg-amber-50/60"
                            : "bg-white/70"
                        }`}
                        title={ownerLabel ? `${item.rationale}\nOwner: ${ownerLabel}` : item.rationale}
                      >
                        <span className="min-w-0 flex-1 truncate">
                          {item.title}
                          {subtaskCount > 0 && (
                            <span className="ml-1 text-[9px] text-[var(--text-muted)]">
                              +{subtaskCount} subtask{subtaskCount === 1 ? "" : "s"}
                            </span>
                          )}
                        </span>
                        <span className="rounded bg-[var(--bg-surface)] px-1 font-mono text-[9px] uppercase text-[var(--text-muted)]">
                          {item.estimated_effort}
                        </span>
                        {task && (
                          <span
                            className={`rounded px-1 text-[9px] uppercase ${
                              isUnassigned
                                ? "bg-amber-100 text-amber-800"
                                : "bg-sky-50 text-[var(--accent)]"
                            }`}
                          >
                            {isUnassigned ? "backlog" : task.status}
                          </span>
                        )}
                        {ownerLabel && (
                          <span
                            className={`truncate rounded px-1 text-[9px] ${
                              isUnassigned
                                ? "text-amber-800"
                                : "text-[var(--text-muted)]"
                            }`}
                            style={{ maxWidth: "8rem" }}
                          >
                            {isUnassigned ? "⏳" : "👤"} {ownerLabel}
                          </span>
                        )}
                      </button>
                    </li>
                  );
                })}
              </ul>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function ReviewerChip({ reviewer }: { reviewer: SprintReviewer }) {
  const tone = toneForReviewer(reviewer.status);
  return (
    <div
      className="min-w-0 rounded border px-1.5 py-1"
      style={{
        borderColor: tone.border,
        background: tone.bg,
      }}
      title={reviewer.detail}
    >
      <div className="flex items-center gap-1 text-[9px] font-medium uppercase tracking-wider" style={{ color: tone.fg }}>
        <span>{statusMark(reviewer.status)}</span>
        <span className="truncate">{reviewer.label}</span>
      </div>
      <div className="mt-0.5 truncate text-[9px] text-[var(--text-muted)]">{reviewer.detail}</div>
    </div>
  );
}

function RiskPill({ risk }: { risk: "low" | "medium" | "high" }) {
  const tone = risk === "high" ? "danger" : risk === "medium" ? "warn" : "success";
  return <Pill label={risk} tone={tone} />;
}

function PriorityPill({
  priority,
  expedited,
}: {
  priority: SprintCard["priority"];
  expedited: boolean;
}) {
  const tone = priority === "p1" ? "danger" : priority === "p2" ? "warn" : "muted";
  return <Pill label={expedited ? `${priority} fast` : priority} tone={tone} />;
}

function Pill({
  label,
  tone,
}: {
  label: string;
  tone: "success" | "warn" | "danger" | "audit" | "muted";
}) {
  const styles: Record<typeof tone, { bg: string; fg: string }> = {
    success: { bg: "color-mix(in srgb, var(--state-success) 16%, transparent)", fg: "var(--state-success)" },
    warn: { bg: "color-mix(in srgb, var(--state-warn) 16%, transparent)", fg: "var(--state-warn)" },
    danger: { bg: "color-mix(in srgb, var(--state-danger) 16%, transparent)", fg: "var(--state-danger)" },
    audit: { bg: "color-mix(in srgb, var(--role-audit) 16%, transparent)", fg: "var(--role-audit)" },
    muted: { bg: "var(--bg-surface)", fg: "var(--text-muted)" },
  };
  const s = styles[tone];
  return (
    <span
      className="rounded px-1.5 py-0.5 text-[9px] font-medium uppercase tracking-wider"
      style={{ background: s.bg, color: s.fg }}
    >
      {label}
    </span>
  );
}

function liveDuration(startedAtIso: string, now: number = Date.now()): string {
  const elapsed = Math.max(0, now - new Date(startedAtIso).getTime());
  const totalSec = Math.round(elapsed / 1000);
  if (totalSec < 60) return `${totalSec}s`;
  const min = Math.floor(totalSec / 60);
  const sec = totalSec % 60;
  if (min < 60) return sec ? `${min}m ${sec}s` : `${min}m`;
  const hr = Math.floor(min / 60);
  const remMin = min % 60;
  return remMin ? `${hr}h ${remMin}m` : `${hr}h`;
}

function toneForReview(status: SprintCard["review_status"]): "success" | "warn" | "danger" | "audit" | "muted" {
  switch (status) {
    case "crew_approved":
    case "merged":
      return "success";
    case "changes_requested":
      return "danger";
    case "superseded":
    case "closed":
    case "fix_queued":
    case "needs_operator":
      return "warn";
    case "crew_reviewing":
    case "ci_running":
      return "audit";
    default:
      return "muted";
  }
}

function toneForReviewer(status: SprintReviewer["status"]): { bg: string; fg: string; border: string } {
  switch (status) {
    case "approved":
      return {
        bg: "color-mix(in srgb, var(--state-success) 10%, transparent)",
        fg: "var(--state-success)",
        border: "color-mix(in srgb, var(--state-success) 30%, var(--line))",
      };
    case "changes_requested":
    case "blocked":
      return {
        bg: "color-mix(in srgb, var(--state-danger) 10%, transparent)",
        fg: "var(--state-danger)",
        border: "color-mix(in srgb, var(--state-danger) 30%, var(--line))",
      };
    case "reviewing":
      return {
        bg: "color-mix(in srgb, var(--role-audit) 10%, transparent)",
        fg: "var(--role-audit)",
        border: "color-mix(in srgb, var(--role-audit) 30%, var(--line))",
      };
    default:
      return {
        bg: "transparent",
        fg: "var(--text-muted)",
        border: "var(--line)",
      };
  }
}

function statusMark(status: SprintReviewer["status"]): string {
  switch (status) {
    case "approved":
      return "✓";
    case "changes_requested":
      return "!";
    case "blocked":
      return "×";
    case "reviewing":
      return "…";
    default:
      return "○";
  }
}

function truncate(s: string, max: number): string {
  return s.length > max ? s.slice(0, max - 1) + "…" : s;
}

function formatAgeMinutes(minutes: number): string {
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${Math.round(minutes)}m ago`;
  const hours = minutes / 60;
  if (hours < 24) return `${Math.round(hours)}h ago`;
  const days = hours / 24;
  return `${Math.round(days)}d ago`;
}

function JargonLegend() {
  // Short glossary for the chips and labels scattered through the board.
  // Kept inline (not a tooltip-per-chip) so investors / new operators can
  // scan the whole vocabulary at a glance.
  const items: Array<{ term: string; meaning: string }> = [
    { term: "low / medium / high", meaning: "Risk level of the change. Drives reviewer set + auto-merge eligibility." },
    { term: "p1 / p2 / p3", meaning: "Priority. p1 = exec/board ask, p2 = principal/PM ask, p3 = default." },
    { term: "DA", meaning: "Devil's Advocate ran on this plan and surfaced a critique." },
    { term: "SEC", meaning: "Security Champion review attached (auto for risk ≥ medium)." },
    { term: "Changes requested", meaning: "A crew reviewer asked for fixes; owner agent is iterating in-place." },
    { term: "iter N · ci|conflict|review", meaning: "Owner agent has re-dispatched N times. Tag explains why (CI fail / merge conflict / reviewer fix)." },
    { term: "⚡ expedited", meaning: "Bypasses the slow approval lane (exec request or auto-fix)." },
  ];
  return (
    <section className="mt-6 rounded-lg border border-[var(--line)] bg-[var(--bg-canvas)] p-3">
      <div className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-[var(--text-muted)]">
        Legend
      </div>
      <dl className="grid grid-cols-1 gap-x-6 gap-y-1.5 text-[11px] sm:grid-cols-2 lg:grid-cols-3">
        {items.map((item) => (
          <div key={item.term} className="flex gap-2">
            <dt className="shrink-0 font-mono text-[var(--text-primary)]">{item.term}</dt>
            <dd className="text-[var(--text-muted)]">— {item.meaning}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function formatFailureKind(kind: string): string {
  switch (kind) {
    case "ci_failure":
      return "ci";
    case "merge_conflict":
      return "conflict";
    case "review_changes_requested":
      return "review";
    default:
      return kind;
  }
}
