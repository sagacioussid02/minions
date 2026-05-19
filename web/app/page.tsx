import { Floor } from "@/components/floor/Floor";
import { River } from "@/components/river/River";
import { Sidebar } from "@/components/sidebar/Sidebar";
import { HeroEvent } from "@/components/hero/HeroEvent";
import { InvestorDemoMode } from "@/components/demo/InvestorDemoMode";
import { CompanyRhythm } from "@/components/rhythm/CompanyRhythm";
import { AmbientParticles } from "@/components/AmbientParticles";
import { HeartbeatDot } from "@/components/HeartbeatDot";
import {
  costSummary,
  getHeroEvent,
  headlineCounters,
  listAgilePanel,
  listActiveAgents,
  listOpenQuestions,
  listRecentEvents,
} from "@/lib/queries";

export const dynamic = "force-dynamic"; // always SSR fresh

export default async function Home() {
  // Initial paint server-rendered from a single snapshot. Client takes over
  // with TanStack Query 3-second polling after hydration.
  const [agents, events, cost, headline, questions, hero, agile] = await Promise.all([
    listActiveAgents(),
    listRecentEvents({ limit: 200 }),
    costSummary(),
    headlineCounters(),
    listOpenQuestions(),
    getHeroEvent(),
    listAgilePanel(),
  ]);

  return (
    <div className="relative flex min-h-screen">
      <Sidebar
        initialCost={cost}
        initialHeadline={headline}
        initialQuestions={questions}
      />
      <main className="relative flex-1 overflow-y-auto">
        <AmbientParticles />
        <header className="relative flex items-center justify-between border-b border-[var(--line)] px-5 py-3">
          <div className="flex items-center gap-3">
            <span className="font-mono text-base tracking-tight text-[var(--accent)]">
              ⌬ minions
            </span>
            <span className="text-sm text-[var(--text-muted)]">HQ</span>
          </div>
          <div className="flex items-center gap-2">
            <a
              href="/stage"
              className="rounded-md border border-[var(--line)] px-3 py-1 text-xs uppercase tracking-wider text-[var(--text-muted)] hover:border-[var(--accent)]/40 hover:text-[var(--text-primary)]"
            >
              stage
            </a>
            <a
              href="/leadership"
              className="rounded-md border border-[var(--line)] px-3 py-1 text-xs uppercase tracking-wider text-[var(--text-muted)] hover:border-[var(--accent)]/40 hover:text-[var(--text-primary)]"
            >
              leadership
            </a>
            <a
              href="/sprint"
              className="rounded-md border border-[var(--line)] px-3 py-1 text-xs uppercase tracking-wider text-[var(--text-muted)] hover:border-[var(--accent)]/40 hover:text-[var(--text-primary)]"
            >
              sprint
            </a>
            <a
              href="/replay"
              className="rounded-md border border-[var(--line)] px-3 py-1 text-xs uppercase tracking-wider text-[var(--text-muted)] hover:border-[var(--accent)]/40 hover:text-[var(--text-primary)]"
            >
              replay
            </a>
            <HeartbeatDot />
          </div>
        </header>
        <div className="relative mx-auto flex w-full max-w-[1500px] flex-col gap-4 p-4 xl:p-5">
          <HeroEvent event={hero} />
          <InvestorDemoMode initialEvents={events} />
          <CompanyRhythm initialAgile={agile} agents={agents} events={events} />
          <Floor initial={agents} />
          <River initial={events} />
        </div>
      </main>
    </div>
  );
}
