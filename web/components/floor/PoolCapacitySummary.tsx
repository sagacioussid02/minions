import { type PoolCapacity } from "@/lib/pools";

const POOL_LABELS: Record<string, string> = {
  engineering: "Engineering",
  qa: "QA",
  devops: "DevOps",
  security: "Security",
};

/**
 * Live-presence capacity, by pool — a separate axis from the
 * executive/specialist/delivery/audit `CapacitySummary` above it. Shows
 * running/reviewing/blocked agents as distinct from idle "available" ones,
 * which the older bucket summary can't (it only knows assigned vs total).
 */
export function PoolCapacitySummary({ capacity }: { capacity: PoolCapacity[] }) {
  return (
    <div className="mb-4 grid grid-cols-1 gap-2 md:grid-cols-2 xl:grid-cols-4">
      {capacity.map((item) => {
        const total =
          item.running + item.blocked + item.reviewing + item.assigned + item.available;
        return (
          <div
            key={item.pool}
            className="rounded-xl border border-[var(--line)] bg-white/70 p-3"
          >
            <div className="text-[10px] font-semibold uppercase tracking-wider text-[var(--text-muted)]">
              {POOL_LABELS[item.pool] ?? item.pool}
            </div>
            <div className="mt-1 text-xl font-semibold text-[var(--text-primary)]">
              {item.available}/{total} <span className="text-xs font-normal text-[var(--text-muted)]">available</span>
            </div>
            <div className="mt-2 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-[var(--text-muted)]">
              {item.running > 0 && <span>{item.running} running</span>}
              {item.blocked > 0 && <span className="text-rose-600">{item.blocked} blocked</span>}
              {item.reviewing > 0 && <span>{item.reviewing} reviewing</span>}
              {item.assigned > 0 && <span>{item.assigned} assigned</span>}
            </div>
          </div>
        );
      })}
    </div>
  );
}
