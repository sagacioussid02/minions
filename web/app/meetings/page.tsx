import Link from "next/link";
import { HeartbeatDot } from "@/components/HeartbeatDot";
import { listMeetings } from "@/lib/queries";
import { MeetingsList } from "@/components/meetings/MeetingsList";

export const dynamic = "force-dynamic";
export const metadata = {
  title: "Meetings — agents at work",
};

export default async function MeetingsPage() {
  // 2-day window. Older runs are archived (not shown).
  const meetings = await listMeetings({ windowMinutes: 48 * 60 });

  return (
    <div className="relative flex min-h-screen flex-col">
      <header className="relative flex items-center justify-between border-b border-[var(--line)] px-6 py-3">
        <div className="flex items-center gap-3">
          <Link href="/" className="font-mono text-sm tracking-tight text-[var(--accent)]">
            ⌬ minions
          </Link>
          <span className="text-xs text-[var(--text-muted)]">meetings</span>
        </div>
        <div className="flex items-center gap-4">
          <Link
            href="/sprint"
            className="rounded border border-[var(--line)] px-2 py-0.5 text-[10px] uppercase tracking-wider text-[var(--text-muted)] hover:border-[var(--accent)]/40 hover:text-[var(--text-primary)]"
          >
            sprint
          </Link>
          <Link
            href="/roster"
            className="rounded border border-[var(--line)] px-2 py-0.5 text-[10px] uppercase tracking-wider text-[var(--text-muted)] hover:border-[var(--accent)]/40 hover:text-[var(--text-primary)]"
          >
            roster
          </Link>
          <Link
            href="/leadership"
            className="rounded border border-[var(--line)] px-2 py-0.5 text-[10px] uppercase tracking-wider text-[var(--text-muted)] hover:border-[var(--accent)]/40 hover:text-[var(--text-primary)]"
          >
            leadership
          </Link>
          <HeartbeatDot />
        </div>
      </header>
      <main className="flex-1 overflow-y-auto p-6">
        <MeetingsList meetings={meetings} />
      </main>
    </div>
  );
}
