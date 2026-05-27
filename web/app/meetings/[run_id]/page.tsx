import Link from "next/link";
import { notFound } from "next/navigation";
import { HeartbeatDot } from "@/components/HeartbeatDot";
import { getMeeting } from "@/lib/queries";
import { LiveMeeting } from "@/components/meetings/LiveMeeting";

export const dynamic = "force-dynamic";

export default async function MeetingPage({
  params,
}: {
  params: Promise<{ run_id: string }>;
}) {
  const { run_id } = await params;
  const meeting = await getMeeting(run_id);
  if (!meeting) {
    notFound();
  }

  return (
    <div className="relative flex min-h-screen flex-col">
      <header className="relative flex items-center justify-between border-b border-[var(--line)] px-6 py-3">
        <div className="flex items-center gap-3">
          <Link href="/" className="font-mono text-sm tracking-tight text-[var(--accent)]">
            ⌬ minions
          </Link>
          <Link
            href="/meetings"
            className="text-xs text-[var(--text-muted)] hover:text-[var(--text-primary)]"
          >
            meetings
          </Link>
          <span className="text-xs text-[var(--text-muted)]">/</span>
          <span className="font-mono text-xs text-[var(--text-primary)]">
            {run_id.slice(0, 8)}
          </span>
        </div>
        <HeartbeatDot />
      </header>
      <main className="flex-1 overflow-y-auto p-6">
        <LiveMeeting initial={meeting} />
      </main>
    </div>
  );
}
