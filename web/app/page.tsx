import { Floor } from "@/components/floor/Floor";
import { River } from "@/components/river/River";
import { Sidebar } from "@/components/sidebar/Sidebar";
import {
  costSummary,
  headlineCounters,
  listActiveAgents,
  listOpenQuestions,
  listRecentEvents,
} from "@/lib/queries";

export const dynamic = "force-dynamic"; // always SSR fresh

export default async function Home() {
  // Initial paint server-rendered from a single snapshot. Client takes over
  // with TanStack Query 3-second polling after hydration.
  const [agents, events, cost, headline, questions] = await Promise.all([
    listActiveAgents(),
    listRecentEvents({ limit: 200 }),
    costSummary(),
    headlineCounters(),
    listOpenQuestions(),
  ]);

  return (
    <div className="flex min-h-screen">
      <Sidebar
        initialCost={cost}
        initialHeadline={headline}
        initialQuestions={questions}
      />
      <main className="flex-1 overflow-hidden">
        <header className="flex items-center justify-between border-b border-[var(--line)] px-6 py-3">
          <div className="flex items-center gap-3">
            <span className="font-mono text-sm tracking-tight text-[var(--accent)]">
              ⌬ minions
            </span>
            <span className="text-xs text-[var(--text-muted)]">HQ</span>
          </div>
          <div className="text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
            live
          </div>
        </header>
        <div className="flex flex-col gap-4 p-6">
          <Floor initial={agents} />
          <River initial={events} />
        </div>
      </main>
    </div>
  );
}
