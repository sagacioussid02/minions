"use client";

import { useQuery } from "@tanstack/react-query";
import {
  type CostSummary,
  type HeadlineCounters,
  type Question,
} from "@/lib/schemas";
import { prettyRole } from "@/lib/roles";

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

  return (
    <aside className="flex w-72 shrink-0 flex-col gap-4 border-r border-[var(--line)] bg-[var(--bg-surface)] p-4">
      <CostGauge summary={cost.data} />
      <Counters c={headline.data} />
      <QuestionsInbox qs={questions.data.questions} />
    </aside>
  );
}

function CostGauge({ summary }: { summary: CostSummary }) {
  const frac = Math.min(1, summary.fraction_of_week_cap || 0);
  const tone =
    frac >= 0.9
      ? "var(--state-danger)"
      : frac >= 0.7
        ? "var(--state-warn)"
        : "var(--state-success)";
  const dash = 2 * Math.PI * 36;
  const offset = dash * (1 - frac);
  return (
    <div className="rounded-xl border border-[var(--line)] bg-[var(--bg-elevated)] p-4">
      <div className="mb-2 text-xs uppercase tracking-wider text-[var(--text-muted)]">
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
          <div className="text-xl font-medium tabular-nums">
            ${summary.week_to_date_usd.toFixed(2)}
          </div>
          <div className="text-xs text-[var(--text-muted)]">
            of ${summary.week_cap_usd.toFixed(0)} cap
          </div>
          <div className="mt-1 text-[10px] text-[var(--text-muted)]">
            today ${summary.today_usd.toFixed(2)}
          </div>
        </div>
      </div>
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
      <div className="mb-2 text-xs uppercase tracking-wider text-[var(--text-muted)]">
        Right now
      </div>
      <dl className="grid grid-cols-2 gap-y-2">
        {items.map(([label, n]) => (
          <div key={label}>
            <dt className="text-[10px] text-[var(--text-muted)]">{label}</dt>
            <dd className="text-base font-medium tabular-nums">{n}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function QuestionsInbox({ qs }: { qs: Question[] }) {
  return (
    <div className="rounded-xl border border-[var(--line)] bg-[var(--bg-elevated)] p-4">
      <div className="mb-2 flex items-center gap-2 text-xs uppercase tracking-wider text-[var(--text-muted)]">
        Questions for you
        <span className="ml-auto rounded bg-[var(--bg-surface)] px-1.5 font-mono text-[10px]">
          {qs.length}
        </span>
      </div>
      {qs.length === 0 ? (
        <p className="text-xs text-[var(--text-muted)]">No open questions.</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {qs.slice(0, 5).map((q) => (
            <li key={q.id} className="rounded-md border border-[var(--line)] p-2">
              <div className="flex items-center gap-1.5 text-[10px] text-[var(--text-muted)]">
                <span>{q.project}</span>
                <span>·</span>
                <span>{prettyRole(q.asker_role)}</span>
                {q.status === "escalated" && (
                  <span className="ml-auto rounded bg-[var(--state-warn)]/15 px-1 text-[var(--state-warn)]">
                    escalated
                  </span>
                )}
              </div>
              <div className="mt-0.5 line-clamp-2 text-xs">{q.question}</div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
