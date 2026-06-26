import Link from "next/link";
import { notFound } from "next/navigation";
import { HeartbeatDot } from "@/components/HeartbeatDot";
import { getMeeting } from "@/lib/queries";
import { Meeting3DClient } from "@/components/meetings/Meeting3DClient";

export const dynamic = "force-dynamic";

export default async function Meeting3DPage({
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
          <Link href="/hq" className="font-mono text-sm tracking-tight text-[var(--accent)]">
            ⌬ minions
          </Link>
          <Link
            href="/hq/meetings"
            className="text-xs text-[var(--text-muted)] hover:text-[var(--text-primary)]"
          >
            meetings
          </Link>
          <span className="text-xs text-[var(--text-muted)]">/</span>
          <Link
            href={`/hq/meetings/${run_id}`}
            className="font-mono text-xs text-[var(--text-primary)] hover:text-[var(--accent)]"
          >
            {run_id.slice(0, 8)}
          </Link>
          <span className="text-xs text-[var(--text-muted)]">/ 3d</span>
        </div>
        <HeartbeatDot />
      </header>
      <main className="flex-1 overflow-y-auto p-6">
        <div className="mx-auto max-w-7xl">
          <Meeting3DClient initial={meeting} backHref={`/hq/meetings/${run_id}`} />
        </div>
      </main>
    </div>
  );
}
