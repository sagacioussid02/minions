import { Suspense } from "react";
import { SprintBoard } from "@/components/sprint/SprintBoard";
import { AmbientParticles } from "@/components/AmbientParticles";
import { HeartbeatDot } from "@/components/HeartbeatDot";
import { listSprintBoard } from "@/lib/queries";
import type { SprintWindow } from "@/lib/schemas";
import Link from "next/link";

export const dynamic = "force-dynamic";

const WINDOWS = new Set<SprintWindow>([
  "this_week",
  "last_week",
  "last_30d",
  "last_90d",
  "all",
]);

export default async function SprintPage({
  searchParams,
}: {
  searchParams: Promise<{ [key: string]: string | string[] | undefined }>;
}) {
  // SSR the exact board the URL asks for, so a shared/bookmarked link paints
  // the right project + window without a fetch flash. The client (SprintBoard)
  // keeps everything else in the URL from here.
  const sp = await searchParams;
  const project = typeof sp.project === "string" ? sp.project : null;
  const windowParam = typeof sp.window === "string" ? sp.window : "";
  const window = WINDOWS.has(windowParam as SprintWindow)
    ? (windowParam as SprintWindow)
    : "this_week";
  const board = await listSprintBoard(project ?? undefined, window);
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
              href="/hq/roster"
              className="rounded border border-[var(--line)] px-2 py-0.5 text-[10px] uppercase tracking-wider text-[var(--text-muted)] hover:border-[var(--accent)]/40 hover:text-[var(--text-primary)]"
            >
              roster
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
          <Suspense fallback={null}>
            <SprintBoard
              initial={board}
              initialProject={project}
              initialWindow={window}
            />
          </Suspense>
        </div>
      </main>
    </div>
  );
}
