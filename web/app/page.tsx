import type { Metadata } from "next";
import Link from "next/link";
import { auth } from "@clerk/nextjs/server";
import { redirect } from "next/navigation";

export const metadata: Metadata = {
  title: "minions — your autonomous AI engineering org",
  description:
    "Connect a GitHub repo and a crew of AI agents plans, builds, and ships pull requests while you stay the one human in the loop.",
};

// Public marketing landing rendered at `/`. Intentionally static: it runs
// NO tenant-scoped query and issues NO fetch that would 401 for an
// unauthenticated visitor (see public-saas-onboarding spec). The dashboard
// lives under `/hq/*`.
//
// Auth (P2) is live: CTAs go to Clerk's sign-in / sign-up, and a signed-in
// visitor is server-redirected from `/` to `/hq` (see the redirect in the
// component below) with no client flash.
const SIGN_UP_HREF = "/sign-up";
const SIGN_IN_HREF = "/sign-in";

const STEPS: ReadonlyArray<readonly [string, string, string]> = [
  [
    "Connect a repo",
    "Sign in with GitHub and install the minions app on the repos you want worked on.",
    "01",
  ],
  [
    "Approve the plan",
    "A planning crew proposes a sprint as a decision record. You approve, tweak, or reject.",
    "02",
  ],
  [
    "Watch it ship",
    "Engineer agents open pull requests for the approved work. You stay the one human in the loop.",
    "03",
  ],
];

export default async function MarketingLanding() {
  // Signed-in visitors never see the marketing page — straight to HQ,
  // server-side, no client flash.
  const { userId } = await auth();
  if (userId) redirect("/hq");

  return (
    <div className="flex min-h-screen flex-col">
      <header className="flex items-center justify-between border-b border-[var(--line)] px-6 py-4">
        <div className="flex items-center gap-2">
          <span className="font-mono text-base tracking-tight text-[var(--accent)]">
            ⌬ minions
          </span>
        </div>
        <Link
          href={SIGN_IN_HREF}
          className="rounded-md border border-[var(--line)] px-3 py-1.5 text-xs font-medium uppercase tracking-wider text-[var(--text-muted)] hover:border-[var(--accent)]/40 hover:text-[var(--text-primary)]"
        >
          Sign in
        </Link>
      </header>

      <main className="relative flex-1">
        <section className="mx-auto flex w-full max-w-[1100px] flex-col items-center px-6 pb-16 pt-20 text-center">
          <span className="mb-6 rounded-full border border-[var(--line)] bg-[var(--bg-elevated)] px-3 py-1 text-xs uppercase tracking-wider text-[var(--text-muted)]">
            Autonomous AI engineering org
          </span>
          <h1 className="max-w-3xl text-balance text-4xl font-semibold leading-tight tracking-tight text-[var(--text-primary)] sm:text-5xl">
            A crew of AI agents that ships your software
          </h1>
          {/* Exactly the two-sentence pitch the spec calls for. */}
          <p className="mt-6 max-w-2xl text-balance text-lg text-[var(--text-muted)]">
            minions is an autonomous AI engineering org that plans, builds, and
            ships across your GitHub repos — while you stay the one human in the
            loop. Connect a repo, approve the work, and watch a crew of agents
            open the pull requests.
          </p>
          <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
            <Link
              href={SIGN_UP_HREF}
              className="rounded-md bg-[var(--accent)] px-5 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:opacity-90"
            >
              Sign up
            </Link>
            <Link
              href={SIGN_IN_HREF}
              className="rounded-md border border-[var(--line)] px-5 py-2.5 text-sm font-semibold text-[var(--text-primary)] transition hover:border-[var(--accent)]/40"
            >
              Sign in
            </Link>
          </div>

          <ConsolePreview />
        </section>

        <section className="mx-auto w-full max-w-[1100px] px-6 pb-24">
          <div className="grid gap-4 sm:grid-cols-3">
            {STEPS.map(([title, body, n]) => (
              <div
                key={n}
                className="rounded-xl border border-[var(--line)] bg-[var(--bg-elevated)] p-5 text-left"
              >
                <div className="mb-3 font-mono text-xs text-[var(--accent)]">
                  {n}
                </div>
                <div className="mb-1.5 text-sm font-semibold text-[var(--text-primary)]">
                  {title}
                </div>
                <p className="text-sm leading-relaxed text-[var(--text-muted)]">
                  {body}
                </p>
              </div>
            ))}
          </div>
        </section>
      </main>

      <footer className="border-t border-[var(--line)] px-6 py-6 text-center text-xs text-[var(--text-muted)]">
        <span className="font-mono text-[var(--accent)]">⌬ minions</span> · every
        meaningful action goes through a human approval gate.
      </footer>
    </div>
  );
}

// A static, dependency-free mock of the HQ console used as the landing's
// "screenshot" until a real captured image is dropped into /public. Pure
// markup — no data, no client hooks.
function ConsolePreview() {
  return (
    <div className="mt-14 w-full max-w-[960px]">
      <div className="overflow-hidden rounded-xl border border-[var(--line)] bg-[var(--bg-surface)] shadow-lg">
        <div className="flex items-center gap-2 border-b border-[var(--line)] px-4 py-2.5">
          <span className="h-2.5 w-2.5 rounded-full bg-[var(--state-danger)]" />
          <span className="h-2.5 w-2.5 rounded-full bg-[var(--state-warn)]" />
          <span className="h-2.5 w-2.5 rounded-full bg-[var(--state-success)]" />
          <span className="ml-3 font-mono text-xs text-[var(--text-muted)]">
            ⌬ minions HQ — Live
          </span>
        </div>
        <div className="flex">
          <aside className="hidden w-44 shrink-0 flex-col gap-2 border-r border-[var(--line)] p-3 sm:flex">
            {["Live", "Stage", "Sprint", "Roster", "Meetings", "Sentry"].map(
              (label, i) => (
                <div
                  key={label}
                  className={`rounded-md border px-2 py-1.5 text-center text-[10px] font-medium uppercase tracking-wider ${
                    i === 0
                      ? "border-[var(--accent)] bg-[var(--accent)]/10 text-[var(--text-primary)]"
                      : "border-[var(--line)] text-[var(--text-muted)]"
                  }`}
                >
                  {label}
                </div>
              ),
            )}
          </aside>
          <div className="flex-1 space-y-3 p-4">
            <div className="h-3 w-1/3 rounded bg-[var(--line)]" />
            <div className="grid grid-cols-3 gap-3">
              {[0, 1, 2].map((i) => (
                <div
                  key={i}
                  className="rounded-lg border border-[var(--line)] bg-[var(--bg-elevated)] p-3"
                >
                  <div className="mb-2 h-2 w-2/3 rounded bg-[var(--line)]" />
                  <div className="h-6 w-1/2 rounded bg-[var(--accent)]/30" />
                </div>
              ))}
            </div>
            <div className="space-y-2 rounded-lg border border-[var(--line)] bg-[var(--bg-elevated)] p-3">
              {[0, 1, 2, 3].map((i) => (
                <div key={i} className="flex items-center gap-2">
                  <span className="h-2 w-2 rounded-full bg-[var(--state-success)]" />
                  <div
                    className="h-2 rounded bg-[var(--line)]"
                    style={{ width: `${70 - i * 12}%` }}
                  />
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
