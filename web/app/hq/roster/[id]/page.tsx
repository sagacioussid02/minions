import Link from "next/link";
import { notFound } from "next/navigation";
import { HeartbeatDot } from "@/components/HeartbeatDot";
import { listActiveAgents, listTasksForProject } from "@/lib/queries";
import { RosterDetail } from "@/components/roster/RosterDetail";

export const dynamic = "force-dynamic";

export default async function RosterDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id: rawId } = await params;
  const id = decodeURIComponent(rawId);
  const agents = await listActiveAgents();
  const agent = agents.find((a) => a.id === id);
  if (!agent) {
    notFound();
  }

  // Tasks where this agent is the owner. We pull the project's full task
  // list (cheap) and filter client-side rather than building a per-owner
  // query — owner-indexed retrieval can come later if the volume grows.
  const tasks = agent.project
    ? (await listTasksForProject(agent.project)).filter(
        (t) => t.owner_agent_id === id
      )
    : [];

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
          <Link
            href="/hq/roster"
            className="text-xs text-[var(--text-muted)] hover:text-[var(--text-primary)]"
          >
            roster
          </Link>
          <span className="text-xs text-[var(--text-muted)]">
            /{agent.display_name ?? agent.role}
          </span>
        </div>
        <HeartbeatDot />
      </header>
      <main className="relative flex-1 px-6 py-6">
        <RosterDetail agent={agent} tasks={tasks} />
      </main>
    </div>
  );
}
