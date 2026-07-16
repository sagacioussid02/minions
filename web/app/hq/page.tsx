import { Floor } from "@/components/floor/Floor";
import { River } from "@/components/river/River";
import { Sidebar } from "@/components/sidebar/Sidebar";
import { HeroEvent } from "@/components/hero/HeroEvent";
import { CompanyBenchMeetings } from "@/components/bench/CompanyBenchMeetings";
import { AmbientParticles } from "@/components/AmbientParticles";
import { HeartbeatDot } from "@/components/HeartbeatDot";
import { UserButton } from "@clerk/nextjs";
import { getCurrentTenant } from "@/lib/tenant";
import {
  costSummary,
  getHeroEvent,
  headlineCounters,
  listActiveAgents,
  listMeetings,
  listOpenQuestions,
  listRecentEvents,
} from "@/lib/queries";

export const dynamic = "force-dynamic"; // always SSR fresh

export default async function Home() {
  // Initial paint server-rendered from a single snapshot. Client takes over
  // with TanStack Query 3-second polling after hydration.
  const [tenant, agents, events, cost, headline, questions, hero, meetings] = await Promise.all([
    getCurrentTenant(),
    listActiveAgents(),
    listRecentEvents({ limit: 200 }),
    costSummary(),
    headlineCounters(),
    listOpenQuestions(),
    getHeroEvent(),
    listMeetings({ windowMinutes: 7 * 24 * 60 }),
  ]);

  return (
    <div className="relative flex min-h-screen">
      <Sidebar
        initialCost={cost}
        initialHeadline={headline}
        initialQuestions={questions}
        isFounder={tenant.founder}
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
              href="/hq/sprint"
              className="rounded-md border border-[var(--line)] px-3 py-1 text-xs uppercase tracking-wider text-[var(--text-muted)] hover:border-[var(--accent)]/40 hover:text-[var(--text-primary)]"
            >
              sprint
            </a>
            <a
              href="/hq/replay"
              className="rounded-md border border-[var(--line)] px-3 py-1 text-xs uppercase tracking-wider text-[var(--text-muted)] hover:border-[var(--accent)]/40 hover:text-[var(--text-primary)]"
            >
              replay
            </a>
            <HeartbeatDot />
            <UserButton />
          </div>
        </header>
        <div className="relative mx-auto flex w-full max-w-[1500px] flex-col gap-4 p-4 xl:p-5">
          <HeroEvent event={hero} />
          <CompanyBenchMeetings agents={agents} meetings={meetings} />
          <Floor initial={agents} />
          <River initial={events} />
        </div>
      </main>
    </div>
  );
}
