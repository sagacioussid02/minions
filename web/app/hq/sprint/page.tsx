import { SprintBoard } from "@/components/sprint/SprintBoard";
import { AmbientParticles } from "@/components/AmbientParticles";
import { HeartbeatDot } from "@/components/HeartbeatDot";
import { listSprintBoard } from "@/lib/queries";
import Link from "next/link";

export const dynamic = "force-dynamic";

export default async function SprintPage() {
  const board = await listSprintBoard(undefined, "this_week");
  return (
    <div className="relative flex min-h-screen flex-col">
      <main className="relative flex-1 overflow-hidden">
        <AmbientParticles />
        <header className="relative flex items-center justify-between border-b border-[var(--line)] px-6 py-3">
          <div className="flex items-center gap-3">
            <Link href="/hq" className="font-mono text-sm tracking-tight text-[var(--accent)]">
              ⌬ minions
            </Link>
            <span className="text-xs text-[var(--text-muted)]">sprint board</span>
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
              href="/hq/roster"
              className="rounded border border-[var(--line)] px-2 py-0.5 text-[10px] uppercase tracking-wider text-[var(--text-muted)] hover:border-[var(--accent)]/40 hover:text-[var(--text-primary)]"
            >
              roster
            </Link>
            <Link
              href="/hq/leadership"
              className="rounded border border-[var(--line)] px-2 py-0.5 text-[10px] uppercase tracking-wider text-[var(--text-muted)] hover:border-[var(--accent)]/40 hover:text-[var(--text-primary)]"
            >
              leadership
            </Link>
            <Link
              href="/hq"
              className="rounded border border-[var(--line)] px-2 py-0.5 text-[10px] uppercase tracking-wider text-[var(--text-muted)] hover:border-[var(--accent)]/40 hover:text-[var(--text-primary)]"
            >
              live
            </Link>
            <a
              href="/hq/replay"
              className="rounded border border-[var(--line)] px-2 py-0.5 text-[10px] uppercase tracking-wider text-[var(--text-muted)] hover:border-[var(--accent)]/40 hover:text-[var(--text-primary)]"
            >
              replay
            </a>
            <HeartbeatDot />
          </div>
        </header>
        <div className="relative flex flex-col gap-4 p-6">
          <SprintBoard initial={board} />
        </div>
      </main>
    </div>
  );
}
