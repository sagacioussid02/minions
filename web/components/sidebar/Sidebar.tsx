"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useSyncExternalStore } from "react";
import {
  type CostSummary,
  type HeadlineCounters,
  type Question,
  type SiteHealth,
} from "@/lib/schemas";
import { prettyRole } from "@/lib/roles";

// ---- Stage-opened "new" badge: SSR-safe external store -----------------
//
// localStorage is client-only, so we read it through ``useSyncExternalStore``
// instead of useState. The server snapshot is ``true`` (badge hidden) to
// match first client paint; the client snapshot reads the real flag.
// Subscription bridges the ``minions-stage-opened`` custom event that
// ``components/stage/Stage.tsx`` dispatches the first time the operator
// opens /stage.
const STAGE_OPENED_KEY = "minions-stage-opened";

function subscribeStageOpened(onStoreChange: () => void): () => void {
  if (typeof window === "undefined") return () => {};
  window.addEventListener(STAGE_OPENED_KEY, onStoreChange);
  window.addEventListener("storage", onStoreChange);
  return () => {
    window.removeEventListener(STAGE_OPENED_KEY, onStoreChange);
    window.removeEventListener("storage", onStoreChange);
  };
}

function getStageOpenedClient(): boolean {
  return (
    typeof window !== "undefined" &&
    window.localStorage.getItem(STAGE_OPENED_KEY) === "true"
  );
}

function getStageOpenedServer(): boolean {
  return true; // hide badge during SSR; matches first client paint
}

// Every navigable top-level page in the console, in menu order.
const NAV_ITEMS: ReadonlyArray<readonly [string, string]> = [
  ["/hq", "Live"],
  ["/hq/stage", "Stage"],
  ["/hq/sprint", "Sprint"],
  ["/hq/roster", "Roster"],
  ["/hq/meetings", "Meetings"],
  ["/hq/sentry", "Sentry"],
  ["/hq/leadership", "Leadership"],
  ["/hq/spokesperson", "Spokesperson"],
  ["/hq/replay", "Replay"],
];

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
  // The "new" badge depends on localStorage, which exists only on the
  // client. Initializing useState from `window.localStorage` here causes
  // an SSR hydration mismatch (server: undefined → no badge; client:
  // maybe shows badge). useSyncExternalStore is the idiomatic answer:
  // the server snapshot returns `true` (badge hidden) to match the
  // first client paint, and the client snapshot reads the real value.
  // It also lets us subscribe to the cross-component "minions-stage-
  // opened" custom event without a synchronous setState-in-effect.
  const stageOpened = useSyncExternalStore(
    subscribeStageOpened,
    getStageOpenedClient,
    getStageOpenedServer,
  );

  useEffect(() => {
    if (pathname === "/hq/stage") {
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
          {NAV_ITEMS.map(([href, label]) => {
            const active =
              href === "/hq" ? pathname === "/hq" : pathname.startsWith(href);
            return (
              <Link
                key={href}
                href={href}
                aria-current={active ? "page" : undefined}
                className={`relative rounded-md border px-2 py-1 text-center text-xs font-medium uppercase tracking-wider hover:border-[var(--accent)]/40 hover:text-[var(--text-primary)] ${
                  active
                    ? "border-[var(--accent)] bg-[var(--accent)]/10 text-[var(--text-primary)]"
                    : "border-[var(--line)] text-[var(--text-muted)]"
                }`}
              >
                {label}
                {href === "/hq/stage" && !stageOpened && (
                  <span className="absolute -right-1.5 -top-1 rounded-full bg-[var(--accent)] px-1.5 py-0.5 text-[9px] font-semibold leading-none text-white shadow-sm">
                    new
                  </span>
                )}
              </Link>
            );
          })}
        </div>
      </nav>
      <CostGauge summary={cost.data} />
      <Counters c={headline.data} />
      <SentryTile />
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
          Cap ${summary.week_cap_usd.toFixed(0)}/wk · cost callbacks not firing for CrewAI runs (tracked).
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

// Compact site-health tile. Client-fetches /api/site-health so the home
// page server component doesn't need to take a new prop.
async function fetchSiteHealth(): Promise<SiteHealth> {
  const r = await fetch("/api/site-health", { cache: "no-store" });
  if (!r.ok) throw new Error("site-health fetch failed");
  return r.json();
}

function SentryTile() {
  const q = useQuery({
    queryKey: ["site-health"],
    queryFn: fetchSiteHealth,
    initialData: { projects: [] } as SiteHealth,
    refetchInterval: 30_000,
  });
  const projects = q.data.projects;
  const total = projects.length;
  const green = projects.filter((p) => p.ok).length;
  const allGood = total === 0 || green === total;
  const dot = allGood ? "var(--state-success)" : "var(--state-danger)";

  return (
    <Link
      href="/hq/sentry"
      className="block rounded-xl border border-[var(--line)] bg-[var(--bg-elevated)] p-4 transition-colors hover:border-[var(--accent)]/50"
    >
      <div className="mb-2 flex items-center gap-2 text-sm uppercase tracking-wider text-[var(--text-muted)]">
        Site health
        <span
          className="inline-block h-2 w-2 rounded-full"
          style={{ backgroundColor: dot }}
          aria-hidden
        />
        <span className="ml-auto font-mono text-[10px]">
          {green} / {total}
        </span>
      </div>
      {total === 0 ? (
        <p className="text-xs text-[var(--text-muted)]">
          No checks yet — set <code className="font-mono">deploy.production_url</code> on a project.
        </p>
      ) : allGood ? (
        <p className="text-xs text-[var(--text-success,var(--state-success))]">
          All projects healthy.
        </p>
      ) : (
        <p className="text-xs text-[var(--state-danger)]">
          {total - green} project{total - green === 1 ? "" : "s"} failing — open Sentry.
        </p>
      )}
    </Link>
  );
}

/**
 * Make a question card clickable: jump to the related PR when one exists,
 * otherwise to the project's sprint board so the operator lands on the
 * decision in context.
 */
function QuestionLink({ q, children }: { q: Question; children: React.ReactNode }) {
  const cls =
    "block rounded-md border border-[var(--line)] p-2 transition-colors hover:border-[var(--accent)]/50 hover:bg-[var(--bg-surface)]";
  if (q.related_pr_url) {
    return (
      <a href={q.related_pr_url} target="_blank" rel="noopener noreferrer" className={cls}>
        {children}
      </a>
    );
  }
  return (
    <Link href={`/hq/sprint?project=${encodeURIComponent(q.project)}`} className={cls}>
      {children}
    </Link>
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
            <li key={q.id}>
              <QuestionLink q={q}>
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
              </QuestionLink>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
