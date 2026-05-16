"use client";

import { useMemo, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Avatar } from "@/components/Avatar";
import { type SprintBoard as Board, type SprintCard, type SprintColumn } from "@/lib/schemas";
import { prettyRole } from "@/lib/roles";
import { colorFor, registerProjects } from "@/lib/project-color";

const COLUMN_ORDER: SprintColumn[] = [
  "backlog",
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

async function fetchBoard(project: string | null): Promise<Board> {
  const url = project ? `/api/sprint-board?project=${encodeURIComponent(project)}` : `/api/sprint-board`;
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) throw new Error("sprint board fetch failed");
  return r.json();
}

export function SprintBoard({ initial }: { initial: Board }) {
  const [tab, setTab] = useState<string | null>(null); // null = All

  const { data } = useQuery({
    queryKey: ["sprint-board", tab],
    queryFn: () => fetchBoard(tab),
    initialData: tab === null ? initial : undefined,
    refetchInterval: 5_000,
  });

  const board = data ?? initial;

  // Stable palette assignments.
  registerProjects(board.projects);

  const byColumn = useMemo(() => {
    const m = new Map<SprintColumn, SprintCard[]>();
    for (const c of board.cards) {
      const arr = m.get(c.column) ?? [];
      arr.push(c);
      m.set(c.column, arr);
    }
    return m;
  }, [board.cards]);

  return (
    <div className="flex flex-col gap-4">
      <Tabs current={tab} projects={board.projects} onChange={setTab} />
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-3 xl:grid-cols-6">
        {COLUMN_ORDER.map((col) => (
          <Column
            key={col}
            column={col}
            cards={byColumn.get(col) ?? []}
          />
        ))}
      </div>
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

function Column({ column, cards }: { column: SprintColumn; cards: SprintCard[] }) {
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
          cards.map((c) => <Card key={c.decision_id} card={c} />)
        )}
      </ul>
    </div>
  );
}

function Card({ card }: { card: SprintCard }) {
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
        throw new Error(body.reason ?? `merge failed (${r.status})`);
      }
    },
    onSuccess: invalidate,
  });

  const busy = approve.isPending || reject.isPending || merge.isPending;
  const errMsg = approve.error?.message ?? reject.error?.message ?? merge.error?.message;

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
            <RiskPill risk={card.risk} />
            {card.has_devils_advocate && <Pill label="DA" tone="audit" />}
            {card.has_security_review && <Pill label="SEC" tone="audit" />}
            {card.ci_conclusion && card.ci_conclusion !== "success" && (
              <Pill label={`CI ${card.ci_conclusion}`} tone={card.ci_conclusion === "failure" ? "danger" : "muted"} />
            )}
          </div>
          <div className="mt-0.5 text-xs font-medium text-[var(--text-primary)]" title={card.summary}>
            {truncate(card.summary, 90)}
          </div>
          <div className="mt-1 flex items-center gap-2 text-[10px] text-[var(--text-muted)]">
            <span>{ageLabel}</span>
            {card.pr_url && (
              <a
                href={card.pr_url}
                target="_blank"
                rel="noreferrer"
                className="rounded border border-[var(--line)] px-1.5 py-0.5 font-mono hover:border-[var(--accent)]/40 hover:text-[var(--text-primary)]"
              >
                #{card.pr_number}
              </a>
            )}
          </div>
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

function RiskPill({ risk }: { risk: "low" | "medium" | "high" }) {
  const tone = risk === "high" ? "danger" : risk === "medium" ? "warn" : "success";
  return <Pill label={risk} tone={tone} />;
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
