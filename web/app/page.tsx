import { Floor } from "@/components/floor/Floor";
import { River } from "@/components/river/River";
import { Sidebar } from "@/components/sidebar/Sidebar";
import { HeroEvent } from "@/components/hero/HeroEvent";
import { AmbientParticles } from "@/components/AmbientParticles";
import { HeartbeatDot } from "@/components/HeartbeatDot";
import {
  costSummary,
  getHeroEvent,
  headlineCounters,
  listActiveAgents,
  listOpenQuestions,
  listRecentEvents,
} from "@/lib/queries";

export const dynamic = "force-dynamic"; // always SSR fresh

export default async function Home() {
  // Initial paint server-rendered from a single snapshot. Client takes over
  // with TanStack Query 3-second polling after hydration.
  const [agents, events, cost, headline, questions, hero] = await Promise.all([
    listActiveAgents(),
    listRecentEvents({ limit: 200 }),
    costSummary(),
    headlineCounters(),
    listOpenQuestions(),
    getHeroEvent(),
  ]);

  return (
    <div className="relative flex min-h-screen">
      <Sidebar
        initialCost={cost}
        initialHeadline={headline}
        initialQuestions={questions}
      />
      <main className="relative flex-1 overflow-hidden">
        <AmbientParticles />
        <header className="relative flex items-center justify-between border-b border-[var(--line)] px-6 py-3">
          <div className="flex items-center gap-3">
            <span className="font-mono text-sm tracking-tight text-[var(--accent)]">
              ⌬ minions
            </span>
            <span className="text-xs text-[var(--text-muted)]">HQ</span>
          </div>
          <div className="flex items-center gap-4">
            <a
              href="/sprint"
              className="rounded border border-[var(--line)] px-2 py-0.5 text-[10px] uppercase tracking-wider text-[var(--text-muted)] hover:border-[var(--accent)]/40 hover:text-[var(--text-primary)]"
            >
              sprint
            </a>
            <a
              href="/replay"
              className="rounded border border-[var(--line)] px-2 py-0.5 text-[10px] uppercase tracking-wider text-[var(--text-muted)] hover:border-[var(--accent)]/40 hover:text-[var(--text-primary)]"
            >
              replay
            </a>
            <HeartbeatDot />
          </div>
        </header>
        <div className="relative flex flex-col gap-4 p-6">
          <HeroEvent event={hero} />
          <Floor initial={agents} />
          <River initial={events} />
        </div>
      </main>
    </div>
  );
}
