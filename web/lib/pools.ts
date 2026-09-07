/**
 * Capacity pools — engineering/qa/devops/security.
 *
 * A separate axis from Floor.tsx's executive/specialist/delivery/audit
 * *visual* buckets (bucketForRole/EXECUTIVE_ROLES/etc, used for card
 * grouping) — this mapping exists purely for the live-presence capacity
 * rollup (openspec/changes/live-presence-and-capacity). Mirrors
 * `ROLE_POOL` in src/minions/presence.py on the Python side; keep both in
 * sync if roles are added.
 */

export type Pool = "engineering" | "qa" | "devops" | "security";

export const POOL_NAMES: Pool[] = ["engineering", "qa", "devops", "security"];

// Roles that count as reallocatable delivery capacity. Executive, PM,
// management, and audit roles are deliberately excluded.
export const ROLE_POOL: Record<string, Pool> = {
  principal_engineer: "engineering",
  tech_team_lead: "engineering",
  team_architect: "engineering",
  senior_engineer: "engineering",
  engineer: "engineering",
  intern: "engineering",
  data_engineer: "engineering",
  documentation_engineer: "engineering",
  performance_engineer: "engineering",
  qa_engineer: "qa",
  test_architect: "qa",
  cloud_devops: "devops",
  senior_devops: "devops",
  devsecops: "security",
  security_champion: "security",
};

export function poolForRole(role: string): Pool | null {
  return ROLE_POOL[role] ?? null;
}

export interface PoolCapacity {
  pool: Pool;
  running: number;
  blocked: number;
  reviewing: number;
  assigned: number;
  available: number;
}

/** Bucket already-fetched agents by pool — always returns all 4 pools. */
export function computePoolCapacity(
  agents: Array<{ role: string; status: "running" | "blocked" | "reviewing" | "assigned" | "available" }>,
): PoolCapacity[] {
  const counts = new Map<Pool, PoolCapacity>(
    POOL_NAMES.map((pool) => [
      pool,
      { pool, running: 0, blocked: 0, reviewing: 0, assigned: 0, available: 0 },
    ]),
  );
  for (const agent of agents) {
    const pool = poolForRole(agent.role);
    if (!pool) continue;
    const bucket = counts.get(pool)!;
    bucket[agent.status] += 1;
  }
  return POOL_NAMES.map((pool) => counts.get(pool)!);
}
