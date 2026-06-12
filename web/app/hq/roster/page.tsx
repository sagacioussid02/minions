import Link from "next/link";
import { HeartbeatDot } from "@/components/HeartbeatDot";
import { listActiveAgents } from "@/lib/queries";
import { RosterGrid } from "@/components/roster/RosterGrid";

export const dynamic = "force-dynamic";

export default async function RosterPage() {
  const agents = await listActiveAgents();
  return (
    <div className="relative flex min-h-screen flex-col">
      <header className="relative flex items-center justify-between border-b border-[var(--line)] px-6 py-3">
        <div className="flex items-center gap-3">
          <Link
            href="/hq"
            className="font-mono text-sm tracking-tight text-[var(--accent)]"
          >
            ⌬ minions
          </Link>
          <span className="text-xs text-[var(--text-muted)]">roster</span>
        </div>
        <div className="flex items-center gap-4">
          <Link
            href="/hq/meetings"
            className="rounded border border-[var(--line)] px-2 py-0.5 text-[10px] uppercase tracking-wider text-[var(--text-muted)] hover:border-[var(--accent)]/40 hover:text-[var(--text-primary)]"
          >
            meetings
          </Link>
          <Link
            href="/hq/stage"
            className="rounded border border-[var(--line)] px-2 py-0.5 text-[10px] uppercase tracking-wider text-[var(--text-muted)] hover:border-[var(--accent)]/40 hover:text-[var(--text-primary)]"
          >
            stage
          </Link>
          <Link
            href="/hq/sprint"
            className="rounded border border-[var(--line)] px-2 py-0.5 text-[10px] uppercase tracking-wider text-[var(--text-muted)] hover:border-[var(--accent)]/40 hover:text-[var(--text-primary)]"
          >
            sprint
          </Link>
          <Link
            href="/hq/leadership"
            className="rounded border border-[var(--line)] px-2 py-0.5 text-[10px] uppercase tracking-wider text-[var(--text-muted)] hover:border-[var(--accent)]/40 hover:text-[var(--text-primary)]"
          >
            leadership
          </Link>
          <HeartbeatDot />
        </div>
      </header>
      <main className="relative flex-1 px-6 py-6">
        <h1 className="mb-1 font-mono text-lg tracking-tight">
          crew roster
        </h1>
        <p className="mb-6 text-xs text-[var(--text-muted)]">
          Every named agent across the org. Click anyone to see what they
          are working on right now and what's queued up next.
        </p>
        <RosterGrid initial={agents} />
      </main>
    </div>
  );
}
