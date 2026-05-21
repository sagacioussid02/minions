import Link from "next/link";
import { AmbientParticles } from "@/components/AmbientParticles";
import { HeartbeatDot } from "@/components/HeartbeatDot";
import { SpokespersonConsole } from "@/components/spokesperson/SpokespersonConsole";
import {
  listInterviewThreads,
  listSpokespersonProjects,
  SPOKESPERSON_ROLES,
} from "@/lib/spokesperson";
import { InterviewThreadSchema } from "@/lib/schemas";

export const dynamic = "force-dynamic";

export default async function LeadershipPage() {
  const [projects, rawThreads] = await Promise.all([
    listSpokespersonProjects(),
    listInterviewThreads(),
  ]);
  const threads = rawThreads.map((thread) => InterviewThreadSchema.parse(thread));

  return (
    <div className="relative flex min-h-screen flex-col">
      <main className="relative flex-1 overflow-hidden">
        <AmbientParticles />
        <header className="relative flex items-center justify-between border-b border-[var(--line)] px-4 py-2.5">
          <div className="flex items-center gap-3">
            <Link href="/" className="font-mono text-sm tracking-tight text-[var(--accent)]">
              ⌬ minions
            </Link>
            <span className="text-xs text-[var(--text-muted)]">leadership room</span>
          </div>
          <div className="flex items-center gap-2">
            <Link
              href="/stage"
              className="rounded border border-[var(--line)] px-2 py-0.5 text-[10px] uppercase tracking-wider text-[var(--text-muted)] hover:border-[var(--accent)]/40 hover:text-[var(--text-primary)]"
            >
              stage
            </Link>
            <Link
              href="/"
              className="rounded border border-[var(--line)] px-2 py-0.5 text-[10px] uppercase tracking-wider text-[var(--text-muted)] hover:border-[var(--accent)]/40 hover:text-[var(--text-primary)]"
            >
              live
            </Link>
            <Link
              href="/sprint"
              className="rounded border border-[var(--line)] px-2 py-0.5 text-[10px] uppercase tracking-wider text-[var(--text-muted)] hover:border-[var(--accent)]/40 hover:text-[var(--text-primary)]"
            >
              sprint
            </Link>
            <Link
              href="/replay"
              className="rounded border border-[var(--line)] px-2 py-0.5 text-[10px] uppercase tracking-wider text-[var(--text-muted)] hover:border-[var(--accent)]/40 hover:text-[var(--text-primary)]"
            >
              replay
            </Link>
            <HeartbeatDot />
          </div>
        </header>
        <div className="relative p-4">
          <SpokespersonConsole
            initial={{
              roles: [...SPOKESPERSON_ROLES],
              projects,
              threads,
            }}
          />
        </div>
      </main>
    </div>
  );
}
