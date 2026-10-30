import Link from "next/link";
import { HeartbeatDot } from "@/components/HeartbeatDot";
import { CrewBoard } from "@/components/crew/CrewBoard";
import { QuestionsInbox } from "@/components/crew/QuestionsInbox";
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
          <HeartbeatDot />
        </div>
      </header>
      <main className="relative mx-auto w-full max-w-5xl flex-1 px-6 py-6">
        <div className="flex flex-col gap-6">
          <QuestionsInbox />
          <CrewBoard data={data} meetings={meetings} />
        </div>
      </main>
    </div>
  );
}
