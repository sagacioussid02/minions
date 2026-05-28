"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import {
  type CostSummary,
  type HeadlineCounters,
  type Question,
} from "@/lib/schemas";
import { agentLabel, prettyRole } from "@/lib/roles";

type CostResp = CostSummary;
type HeadlineResp = HeadlineCounters;
type QuestionsResp = { questions: Question[] };

async function fetchCost(): Promise<CostResp> {
  const r = await fetch("/api/cost", { cache: "no-store" });
  if (!r.ok) throw new Error("cost fetch failed");
  return r.json();
}

async function fetchHeadline(): Promise<HeadlineResp> {
  const r = await fetch("/api/headline", { cache: "no-store" });
  if (!r.ok) throw new Error("headline fetch failed");
  return r.json();
}

async function fetchQuestions(): Promise<QuestionsResp> {
  const r = await fetch("/api/questions", { cache: "no-store" });
  if (!r.ok) throw new Error("questions fetch failed");
  return r.json();
}

export function Sidebar({
  initialCost,
  initialHeadline,
  initialQuestions,
}: {
  initialCost: CostResp;
  initialHeadline: HeadlineResp;
  initialQuestions: Question[];
}) {
  const cost = useQuery({
    queryKey: ["cost"],
    queryFn: fetchCost,
    initialData: initialCost,
  });
  const headline = useQuery({
    queryKey: ["headline"],
    queryFn: fetchHeadline,
    initialData: initialHeadline,
  });
  const questions = useQuery({
    queryKey: ["questions"],
    queryFn: fetchQuestions,
    initialData: { questions: initialQuestions },
  });
  const pathname = usePathname();
  const [stageOpened, setStageOpened] = useState(
    () =>
      typeof window === "undefined" ||
      window.localStorage.getItem("minions-stage-opened") === "true",
  );

  useEffect(() => {
    const onOpened = () => setStageOpened(true);
    window.addEventListener("minions-stage-opened", onOpened);
    return () => window.removeEventListener("minions-stage-opened", onOpened);
  }, []);

  useEffect(() => {
    if (pathname === "/stage") {
      window.localStorage.setItem("minions-stage-opened", "true");
    }
  }, [pathname]);

  return (
    <aside className="flex w-80 shrink-0 flex-col gap-4 border-r border-[var(--line)] bg-[var(--bg-surface)] p-4">
      <nav className="rounded-xl border border-[var(--line)] bg-[var(--bg-elevated)] p-3">
        <div className="mb-2 text-xs uppercase tracking-wider text-[var(--text-muted)]">
          Console
        </div>
        <div className="grid grid-cols-2 gap-2">
          {[
            ["/", "Live"],
            ["/stage", "Stage"],
            ["/leadership", "Leadership"],
            ["/sprint", "Sprint"],
          ].map(([href, label]) => (
            <Link
              key={href}
              href={href}
              className="relative rounded-md border border-[var(--line)] px-2 py-1 text-center text-xs font-medium uppercase tracking-wider text-[var(--text-muted)] hover:border-[var(--accent)]/40 hover:text-[var(--text-primary)]"
            >
              {label}
              {href === "/stage" && !stageOpened && (
                <span className="absolute -right-1.5 -top-1 rounded-full bg-[var(--accent)] px-1.5 py-0.5 text-[9px] font-semibold leading-none text-white shadow-sm">
                  new
                </span>
              )}
            </Link>
          ))}
        </div>
      </nav>
      <CostGauge summary={cost.data} />
      <Counters c={headline.data} />
      <QuestionsInbox qs={questions.data.questions} />
    </aside>
  );
}

function CostGauge({ summary }: { summary: CostSummary }) {
  const hasData = summary.week_to_date_usd > 0 || summary.today_usd > 0;
  if (!hasData) {
    return (
      <div className="rounded-xl border border-[var(--line)] bg-[var(--bg-elevated)] p-4">
        <div className="mb-2 text-sm uppercase tracking-wider text-[var(--text-muted)]">
          Spend this week
        </div>
        <div className="text-sm text-[var(--text-muted)]">
          No cost data recorded yet.
        </div>
        <div className="mt-1 text-xs text-[var(--text-muted)]">
          Cap ${summary.week_cap_usd.toFixed(0)}/wk · spend appears here after the next crew run.
        </div>
      </div>
    );
  }
  const frac = Math.min(1, summary.fraction_of_week_cap || 0);
  const tone =
    frac >= 0.9
      ? "var(--state-danger)"
      : frac >= 0.7
        ? "var(--state-warn)"
        : "var(--state-success)";
  const dash = 2 * Math.PI * 36;
  const offset = dash * (1 - frac);
  const breakdown = summary.breakdown ?? [];
  return (
    <div className="rounded-xl border border-[var(--line)] bg-[var(--bg-elevated)] p-4">
      <div className="mb-3 text-sm uppercase tracking-wider text-[var(--text-muted)]">
        Spend this week
      </div>
      <div className="flex items-center gap-4">
        <svg width="84" height="84" viewBox="0 0 84 84" aria-hidden>
          <circle
            cx="42"
            cy="42"
            r="36"
            stroke="var(--line)"
            strokeWidth="6"
            fill="none"
          />
          <circle
            cx="42"
            cy="42"
            r="36"
            stroke={tone}
            strokeWidth="6"
            fill="none"
            strokeDasharray={dash}
            strokeDashoffset={offset}
            transform="rotate(-90 42 42)"
            strokeLinecap="round"
            style={{ transition: "stroke-dashoffset 600ms ease, stroke 220ms ease" }}
          />
        </svg>
        <div>
          <div className="text-2xl font-semibold tabular-nums">
            ${summary.week_to_date_usd.toFixed(2)}
          </div>
          <div className="text-sm text-[var(--text-muted)]">
            of ${summary.week_cap_usd.toFixed(0)} cap
          </div>
          <div className="mt-1 text-xs text-[var(--text-muted)]">
            today ${summary.today_usd.toFixed(2)}
          </div>
        </div>
      </div>
      {breakdown.length > 0 && <CostBreakdown rows={breakdown} />}
    </div>
  );
}

function CostBreakdown({ rows }: { rows: CostSummary["breakdown"] }) {
  const [open, setOpen] = useState(false);
  const total = rows.reduce((acc, r) => acc + r.cost_usd, 0) || 1;
  return (
    <div className="mt-3 border-t border-[var(--line)] pt-2">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between text-xs text-[var(--text-muted)] transition hover:text-[var(--text-primary)]"
        aria-expanded={open}
      >
        <span>by agent ({rows.length})</span>
        <span className="tabular-nums">{open ? "−" : "+"}</span>
      </button>
      {open && (
        <ul className="mt-2 space-y-1.5">
          {rows.map((r) => {
            const pct = Math.round((r.cost_usd / total) * 100);
            return (
              <li key={`${r.project ?? "portfolio"}-${r.role}`} className="text-xs">
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-[var(--text-primary)]">
                    {agentLabel(r.display_name, r.role)}
                  </span>
                  <span className="shrink-0 tabular-nums text-[var(--text-muted)]">
                    ${r.cost_usd.toFixed(2)}
                  </span>
                </div>
                <div className="mt-0.5 flex items-center gap-1.5">
                  <div className="h-1 flex-1 overflow-hidden rounded-full bg-[var(--line)]">
                    <div
                      className="h-full rounded-full bg-[var(--accent)]"
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                  <span className="shrink-0 text-[9px] text-[var(--text-muted)]">
                    {r.project ?? "portfolio"} · {r.calls} calls
                  </span>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function Counters({ c }: { c: HeadlineCounters }) {
  const items: Array<[string, number]> = [
    ["Open PRs", c.open_prs],
    ["Pending approvals", c.pending_approvals],
    ["Agents active (5m)", c.agents_active_5min],
    ["Queued fixes", c.queued_fixes],
  ];
  return (
    <div className="rounded-xl border border-[var(--line)] bg-[var(--bg-elevated)] p-4">
      <div className="mb-3 text-sm uppercase tracking-wider text-[var(--text-muted)]">
        Right now
      </div>
      <dl className="grid grid-cols-2 gap-y-2">
        {items.map(([label, n]) => (
          <div key={label}>
            <dt className="text-xs text-[var(--text-muted)]">{label}</dt>
            <dd className="text-xl font-semibold tabular-nums">{n}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function QuestionsInbox({ qs }: { qs: Question[] }) {
  return (
    <div className="rounded-xl border border-[var(--line)] bg-[var(--bg-elevated)] p-4">
      <div className="mb-3 flex items-center gap-2 text-sm uppercase tracking-wider text-[var(--text-muted)]">
        Questions for you
        <span className="ml-auto rounded bg-[var(--bg-surface)] px-1.5 font-mono text-[10px]">
          {qs.length}
        </span>
      </div>
      {qs.length === 0 ? (
        <p className="text-sm text-[var(--text-muted)]">No open questions.</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {qs.slice(0, 5).map((q) => (
            <li key={q.id} className="rounded-md border border-[var(--line)] p-2">
              <div className="flex items-center gap-1.5 text-xs text-[var(--text-muted)]">
                <span>{q.project}</span>
                <span>·</span>
                <span>{prettyRole(q.asker_role)}</span>
                {q.status === "escalated" && (
                  <span className="ml-auto rounded bg-[var(--state-warn)]/15 px-1 text-[var(--state-warn)]">
                    escalated
                  </span>
                )}
              </div>
              <div className="mt-1 line-clamp-2 text-sm">{q.question}</div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
