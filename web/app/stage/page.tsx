import Link from "next/link";
import { AmbientParticles } from "@/components/AmbientParticles";
import { HeartbeatDot } from "@/components/HeartbeatDot";
import { Stage } from "@/components/stage/Stage";
import { listActiveAgents, listRecentEvents } from "@/lib/queries";

export const dynamic = "force-dynamic";
export const metadata = {
  title: "Minions stage — who's working on what",
};

/**
 * Banner shown above the legacy Stage view announcing the retirement.
 * Resolution Q6 of the living-org-spaces proposal: keep Stage accessible
 * for one release with a redirect banner pointing to the new surfaces,
 * then delete the route + components in the release after.
 */
function StageRetirementBanner() {
  return (
    <div className="border-b border-[var(--accent)]/30 bg-[var(--accent)]/10 px-4 py-3">
      <div className="mx-auto flex max-w-5xl flex-col gap-1 text-sm">
        <div className="font-medium text-[var(--text-primary)]">
          This view is being retired.
        </div>
        <div className="text-xs text-[var(--text-muted)]">
          To see what crews are working on right now (with full round-table
          visualization), open{" "}
          <Link
            href="/meetings"
            className="font-mono text-[var(--accent)] hover:underline"
          >
            /meetings
          </Link>
          . To browse the roster or talk to a specific agent, open{" "}
          <Link
            href="/roster"
            className="font-mono text-[var(--accent)] hover:underline"
          >
            /roster
          </Link>
          .
        </div>
      </div>
    </div>
  );
}

export default async function StagePage() {
  const [agents, events] = await Promise.all([
    listActiveAgents(),
    listRecentEvents({ limit: 160, windowMinutes: 60 }),
  ]);

  return (
    <div className="relative flex min-h-screen flex-col">
      <main className="relative flex-1 overflow-y-auto">
        <AmbientParticles />
        <header className="relative flex items-center justify-between border-b border-[var(--line)] px-4 py-2.5">
          <div className="flex items-center gap-3">
            <Link href="/" className="font-mono text-sm tracking-tight text-[var(--accent)]">
              ⌬ minions
            </Link>
            <span className="text-xs text-[var(--text-muted)]">agent stage</span>
          </div>
          <div className="flex items-center gap-2">
            <Link
              href="/"
              className="rounded border border-[var(--line)] px-2 py-0.5 text-[10px] uppercase tracking-wider text-[var(--text-muted)] hover:border-[var(--accent)]/40 hover:text-[var(--text-primary)]"
            >
              live
            </Link>
            <Link
              href="/leadership"
              className="rounded border border-[var(--line)] px-2 py-0.5 text-[10px] uppercase tracking-wider text-[var(--text-muted)] hover:border-[var(--accent)]/40 hover:text-[var(--text-primary)]"
            >
              leadership
            </Link>
            <Link
              href="/sprint"
              className="rounded border border-[var(--line)] px-2 py-0.5 text-[10px] uppercase tracking-wider text-[var(--text-muted)] hover:border-[var(--accent)]/40 hover:text-[var(--text-primary)]"
            >
              sprint
            </Link>
            <HeartbeatDot />
          </div>
        </header>
        <StageRetirementBanner />
        <Stage initialEvents={events} agents={agents} />
      </main>
    </div>
  );
}
