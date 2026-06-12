import { Floor } from "@/components/floor/Floor";
import { River } from "@/components/river/River";
import { HeroEvent } from "@/components/hero/HeroEvent";
import { AmbientParticles } from "@/components/AmbientParticles";
import { TimeScrubber } from "@/components/replay/TimeScrubber";
import {
  activityTimeRange,
  getHeroEventAt,
  listActiveAgentsAt,
  listRecentEventsAt,
} from "@/lib/queries-asof";

export const dynamic = "force-dynamic";

type SearchParams = { at?: string };

export default async function Replay({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  const { at } = await searchParams;
  const range = await activityTimeRange();

  // Default to the latest moment we have data for, so a fresh load shows the
  // most recent meaningful state and the user can scrub backward.
  const asOf = at ? new Date(at) : new Date(range.latest);

  const [agents, events, hero] = await Promise.all([
    listActiveAgentsAt(asOf),
    listRecentEventsAt(asOf, 200),
    getHeroEventAt(asOf),
  ]);

  return (
    <div className="relative flex min-h-screen flex-col">
      <main className="relative flex-1 overflow-hidden pb-32">
        <AmbientParticles />
        <header className="relative flex items-center justify-between border-b border-[var(--line)] px-6 py-3">
          <div className="flex items-center gap-3">
            <span className="font-mono text-sm tracking-tight text-[var(--accent)]">
              ⌬ minions
            </span>
            <span className="text-xs text-[var(--text-muted)]">replay</span>
          </div>
          <span className="text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
            historical · scrub the timeline below
          </span>
        </header>
        <div className="relative flex flex-col gap-4 p-6">
          <HeroEvent event={hero} />
          <Floor initial={agents} referenceNow={asOf.toISOString()} />
          <River initial={events} />
        </div>
      </main>

      <TimeScrubber
        earliest={range.earliest}
        latest={range.latest}
        current={asOf.toISOString()}
      />
    </div>
  );
}
