import { SprintBoard } from "@/components/sprint/SprintBoard";
import { AmbientParticles } from "@/components/AmbientParticles";
import { HeartbeatDot } from "@/components/HeartbeatDot";
import { listSprintBoard } from "@/lib/queries";
import Link from "next/link";

export const dynamic = "force-dynamic";

export default async function SprintPage() {
  const board = await listSprintBoard();
  return (
    <div className="relative flex min-h-screen flex-col">
      <main className="relative flex-1 overflow-hidden">
        <AmbientParticles />
        <header className="relative flex items-center justify-between border-b border-[var(--line)] px-6 py-3">
          <div className="flex items-center gap-3">
            <Link href="/" className="font-mono text-sm tracking-tight text-[var(--accent)]">
              ⌬ minions
            </Link>
            <span className="text-xs text-[var(--text-muted)]">sprint board</span>
          </div>
          <div className="flex items-center gap-4">
            <Link
              href="/"
              className="rounded border border-[var(--line)] px-2 py-0.5 text-[10px] uppercase tracking-wider text-[var(--text-muted)] hover:border-[var(--accent)]/40 hover:text-[var(--text-primary)]"
            >
              live
            </Link>
            <a
              href="/replay"
              className="rounded border border-[var(--line)] px-2 py-0.5 text-[10px] uppercase tracking-wider text-[var(--text-muted)] hover:border-[var(--accent)]/40 hover:text-[var(--text-primary)]"
            >
              replay
            </a>
            <HeartbeatDot />
          </div>
        </header>
        <div className="relative p-6">
          <SprintBoard initial={board} />
        </div>
      </main>
    </div>
  );
}
