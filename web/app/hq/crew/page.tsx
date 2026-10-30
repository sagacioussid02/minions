import Link from "next/link";
import { HeartbeatDot } from "@/components/HeartbeatDot";
import { CrewBoard } from "@/components/crew/CrewBoard";
import { loadCrewDashboard } from "@/lib/crew-dashboard";
import { describeNextRun, CRON_SCHEDULES } from "@/lib/cron-schedule";

export const dynamic = "force-dynamic";

export default async function CrewPage() {
  const data = await loadCrewDashboard();
  const meetings = [
    { label: "Sprint planning", when: describeNextRun(CRON_SCHEDULES.weekly.expr) },
    { label: "Scrum", when: describeNextRun(CRON_SCHEDULES.scrum.expr) },
  ];
  return (
    <div className="relative flex min-h-screen flex-col">
      <header className="relative flex items-center justify-between border-b border-[var(--line)] px-6 py-3">
        <div className="flex items-center gap-3">
          <Link href="/hq" className="font-mono text-sm tracking-tight text-[var(--accent)]">
            ⌬ minions
          </Link>
          <span className="text-xs text-[var(--text-muted)]">crew</span>
        </div>
        <div className="flex items-center gap-4">
          <Link
            href="/hq/requests"
            className="rounded border border-[var(--line)] px-2 py-0.5 text-[10px] uppercase tracking-wider text-[var(--text-muted)] hover:border-[var(--accent)]/40 hover:text-[var(--text-primary)]"
          >
            ask the crew
          </Link>
          <HeartbeatDot />
        </div>
      </header>
      <main className="relative mx-auto w-full max-w-5xl flex-1 px-6 py-6">
        <CrewBoard data={data} meetings={meetings} />
      </main>
    </div>
  );
}
