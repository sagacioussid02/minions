import type { Metadata } from "next";
import Link from "next/link";
import {
  DEMO_ACTIVITY,
  DEMO_APPROVAL,
  DEMO_ORG,
  DEMO_ROSTER,
  DEMO_SPRINT,
  demoModeEnabled,
} from "@/lib/demo";
import { iconFor, prettyRole, tierFor } from "@/lib/roles";

export const metadata: Metadata = {
  title: "minions — demo console",
  description:
    "A zero-signup, read-only demo of the minions operator console, rendered from sample data. No account, no database.",
};

// The demo is a public, static page: it reads no database, no tenant and
// no session, so it must never be force-dynamic per-request work — but
// the gate depends on env, so evaluate it at request time.
export const dynamic = "force-dynamic";

const STATUS_STYLE: Record<string, string> = {
  working: "bg-[var(--state-success)]",
  reviewing: "bg-[var(--state-warn)]",
  waiting: "bg-[var(--accent)]",
  idle: "bg-[var(--line)]",
};

export default function DemoPage() {
  if (!demoModeEnabled()) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center px-6 text-center">
        <span className="font-mono text-base tracking-tight text-[var(--accent)]">
          ⌬ minions
        </span>
        <h1 className="mt-6 text-2xl font-semibold text-[var(--text-primary)]">
          Demo mode is off
        </h1>
        <p className="mt-3 max-w-md text-sm leading-relaxed text-[var(--text-muted)]">
          This deployment has a database configured, so the sample-data demo
          is disabled. To try it locally, run the console with no{" "}
          <code className="font-mono text-[var(--text-primary)]">
            MINIONS_DATABASE_URL
          </code>{" "}
          set, or set{" "}
          <code className="font-mono text-[var(--text-primary)]">
            MINIONS_DEMO_MODE=1
          </code>
          .
        </p>
        <Link
          href="/"
          className="mt-8 rounded-md border border-[var(--line)] px-4 py-2 text-sm font-semibold text-[var(--text-primary)] hover:border-[var(--accent)]/40"
        >
          Back to home
        </Link>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen flex-col">
      <header className="flex items-center justify-between border-b border-[var(--line)] px-6 py-3">
        <div className="flex items-center gap-3">
          <span className="font-mono text-base tracking-tight text-[var(--accent)]">
            ⌬ minions
          </span>
          <span className="text-sm text-[var(--text-muted)]">HQ — demo</span>
          <span className="rounded-full border border-[var(--state-warn)] bg-[var(--state-warn)]/10 px-2.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-[var(--state-warn)]">
            Demo · sample data
          </span>
        </div>
        <Link
          href="/sign-up"
          className="rounded-md bg-[var(--accent)] px-3 py-1.5 text-xs font-semibold text-white hover:opacity-90"
        >
          Sign up for the real thing
        </Link>
      </header>

      <main className="mx-auto flex w-full max-w-[1100px] flex-1 flex-col gap-6 px-6 py-6">
        <div className="rounded-md border border-[var(--state-warn)]/50 bg-[var(--state-warn)]/10 px-4 py-3 text-sm text-[var(--text-primary)]">
          You&apos;re looking at a read-only demo with sample data — no
          account, no database, nothing connected to a real repo. In a real
          setup this console is live: a crew of AI agents plans sprints,
          opens pull requests, and waits at approval gates like the one
          below until a human says go.
        </div>

        <section className="grid gap-4 sm:grid-cols-4">
          {[
            ["Organisation", DEMO_ORG.name],
            ["Project", DEMO_ORG.project],
            ["PRs shipped this week", String(DEMO_ORG.prsShippedThisWeek)],
            [
              "Spend this week",
              `$${DEMO_ORG.weeklySpendUsd.toFixed(2)} of $${DEMO_ORG.weeklyCapUsd} cap`,
            ],
          ].map(([label, value]) => (
            <div
              key={label}
              className="rounded-xl border border-[var(--line)] bg-[var(--bg-elevated)] p-4"
            >
              <div className="text-[10px] font-medium uppercase tracking-wider text-[var(--text-muted)]">
                {label}
              </div>
              <div className="mt-1.5 font-mono text-sm text-[var(--text-primary)]">
                {value}
              </div>
            </div>
          ))}
        </section>

        <section className="rounded-xl border border-[var(--accent)] bg-[var(--accent)]/5 p-5">
          <div className="flex items-center gap-2">
            <span className="text-[10px] font-semibold uppercase tracking-wider text-[var(--accent)]">
              Approval gate · simulated
            </span>
          </div>
          <h2 className="mt-2 text-base font-semibold text-[var(--text-primary)]">
            {DEMO_APPROVAL.title}
          </h2>
          <p className="mt-1 text-xs text-[var(--text-muted)]">
            Proposed by {prettyRole(DEMO_APPROVAL.proposedBy)} · waiting on a
            human — the crew does no sprint work until this is approved.
          </p>
          <ul className="mt-3 list-disc space-y-1 pl-5 text-sm text-[var(--text-primary)]">
            {DEMO_APPROVAL.summary.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
          <p className="mt-3 rounded-md border border-[var(--line)] bg-[var(--bg-elevated)] px-3 py-2 text-xs text-[var(--text-muted)]">
            {DEMO_APPROVAL.challenge}
          </p>
          <div className="mt-4 flex flex-wrap gap-3">
            <button
              type="button"
              disabled
              title="Disabled in the demo"
              className="cursor-not-allowed rounded-md bg-[var(--accent)] px-4 py-2 text-sm font-semibold text-white opacity-50"
            >
              Approve plan
            </button>
            <button
              type="button"
              disabled
              title="Disabled in the demo"
              className="cursor-not-allowed rounded-md border border-[var(--line)] px-4 py-2 text-sm font-semibold text-[var(--text-primary)] opacity-50"
            >
              Reject
            </button>
            <span className="self-center text-xs text-[var(--text-muted)]">
              Buttons are disabled in the demo — approvals only happen in a
              real, signed-in setup.
            </span>
          </div>
        </section>

        <section>
          <h2 className="mb-1 font-mono text-lg tracking-tight text-[var(--text-primary)]">
            crew roster
          </h2>
          <p className="mb-4 text-xs text-[var(--text-muted)]">
            Every named agent in the sample org, and what each is doing right
            now.
          </p>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {DEMO_ROSTER.map((agent) => (
              <div
                key={agent.role}
                className="rounded-xl border border-[var(--line)] bg-[var(--bg-elevated)] p-4"
              >
                <div className="flex items-center gap-2">
                  <span aria-hidden>{iconFor(tierFor(agent.role))}</span>
                  <span className="text-sm font-semibold text-[var(--text-primary)]">
                    {prettyRole(agent.role)}
                  </span>
                  <span
                    className={`ml-auto h-2 w-2 rounded-full ${STATUS_STYLE[agent.status]}`}
                    title={agent.status}
                  />
                </div>
                <div className="mt-1 text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
                  {agent.status} · {DEMO_ORG.project}
                </div>
                <p className="mt-2 text-xs leading-relaxed text-[var(--text-muted)]">
                  {agent.workingOn}
                </p>
              </div>
            ))}
          </div>
        </section>

        <section>
          <h2 className="mb-1 font-mono text-lg tracking-tight text-[var(--text-primary)]">
            sprint board
          </h2>
          <p className="mb-4 text-xs text-[var(--text-muted)]">
            Sprint 13 wrapping up, sprint 14 at the gate. Sample items from{" "}
            {DEMO_ORG.project}.
          </p>
          <div className="grid gap-3 md:grid-cols-4">
            {DEMO_SPRINT.map((col) => (
              <div
                key={col.stage}
                className="rounded-xl border border-[var(--line)] bg-[var(--bg-surface)] p-3"
              >
                <div className="mb-3 text-[10px] font-semibold uppercase tracking-wider text-[var(--text-muted)]">
                  {col.stage}
                </div>
                <div className="space-y-2">
                  {col.items.map((item) => (
                    <div
                      key={item.title}
                      className="rounded-lg border border-[var(--line)] bg-[var(--bg-elevated)] p-3"
                    >
                      <div className="text-sm font-medium text-[var(--text-primary)]">
                        {item.title}
                      </div>
                      <div className="mt-0.5 text-[10px] uppercase tracking-wider text-[var(--accent)]">
                        {prettyRole(item.ownerRole)}
                      </div>
                      <p className="mt-1.5 text-xs leading-relaxed text-[var(--text-muted)]">
                        {item.detail}
                      </p>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </section>

        <section>
          <h2 className="mb-3 font-mono text-lg tracking-tight text-[var(--text-primary)]">
            recent activity
          </h2>
          <div className="space-y-2 rounded-xl border border-[var(--line)] bg-[var(--bg-elevated)] p-4">
            {DEMO_ACTIVITY.map((entry) => (
              <div key={entry.text} className="flex items-baseline gap-3 text-sm">
                <span className="w-14 shrink-0 font-mono text-xs text-[var(--text-muted)]">
                  {entry.when}
                </span>
                <span className="text-[var(--text-primary)]">{entry.text}</span>
              </div>
            ))}
          </div>
        </section>

        <section className="rounded-xl border border-[var(--line)] bg-[var(--bg-elevated)] p-5 text-sm leading-relaxed text-[var(--text-muted)]">
          <span className="font-semibold text-[var(--text-primary)]">
            What needs a real setup:
          </span>{" "}
          live data needs a Neon database URL, sign-in needs a Clerk app, and
          a working crew needs the GitHub App installed on your repos plus
          the Python crews running. The demo skips all of that on purpose —
          it exists so you can see what the console is before wiring any of
          it up.
        </section>
      </main>

      <footer className="border-t border-[var(--line)] px-6 py-6 text-center text-xs text-[var(--text-muted)]">
        <span className="font-mono text-[var(--accent)]">⌬ minions</span> ·
        demo mode — every meaningful action in the real product goes through
        a human approval gate.
      </footer>
    </div>
  );
}
