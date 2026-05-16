/**
 * Static org roster — mirrors `src/minions/agents/roster.py`.
 *
 * The Floor uses this to scaffold every *configured* agent, not just the
 * ones that have produced activity. Roles with zero events still render
 * as "cold" cards so the operator sees the full ~60-agent org shape.
 *
 * Keep in sync with the Python file. If the Python roster changes,
 * update this list in the same PR.
 */

export const PER_PROJECT_ROLES: Array<{ role: string; seats: number }> = [
  { role: "product_owner", seats: 1 },
  { role: "manager", seats: 1 },
  { role: "principal", seats: 1 },
  { role: "ttl", seats: 1 },
  { role: "sr_engineer", seats: 2 },
  { role: "engineer", seats: 3 },
  { role: "intern", seats: 1 },
  { role: "sr_devops", seats: 1 },
  { role: "security_champion", seats: 1 },
];

export const SHARED_EXECUTIVE: string[] = [
  "ceo",
  "cto",
  "md",
  "org_owner",
];

export const SHARED_SPECIALIST: string[] = [
  "cloud_devops",
  "devsecops",
  "team_architect",
];

export const AUDIT: string[] = [
  "chief_auditor",
  "process_auditor",
  "code_auditor",
  "cost_auditor",
  "devils_advocate",
];

/** Total agent count given a list of active projects. Layman headline. */
export function totalConfiguredAgents(projectCount: number): number {
  const perProject = PER_PROJECT_ROLES.reduce((n, r) => n + r.seats, 0);
  const shared = SHARED_EXECUTIVE.length + SHARED_SPECIALIST.length + AUDIT.length;
  return perProject * projectCount + shared;
}
