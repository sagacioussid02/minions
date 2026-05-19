/**
 * Static org display roster.
 *
 * The public console intentionally presents the company as a shared, dynamic
 * org: executives and senior specialists span the portfolio, while project
 * pods stay lightweight and borrow capacity from shared pools.
 *
 * Python may still run project-scoped roles internally. This file is the
 * investor-facing shape of the org, not a one-card-per-runtime-worker mirror.
 */

export const PER_PROJECT_ROLES: Array<{ role: string; seats: number }> = [
  { role: "product_owner", seats: 1 },
  { role: "manager", seats: 1 },
  { role: "tech_team_lead", seats: 1 },
];

export const SHARED_EXECUTIVE: string[] = [
  "ceo",
  "cto",
  "managing_director",
  "org_owner",
];

export const SHARED_SPECIALIST: Array<{ role: string; seats: number }> = [
  { role: "principal_engineer", seats: 1 },
  { role: "team_architect", seats: 1 },
  { role: "cloud_devops", seats: 1 },
  { role: "senior_devops", seats: 1 },
  { role: "devsecops", seats: 1 },
  { role: "security_champion", seats: 1 },
  { role: "qa_engineer", seats: 1 },
];

export const SHARED_ENGINEERING_POOL: Array<{ role: string; seatsPerProject: number }> = [
  { role: "senior_engineer", seatsPerProject: 1 },
  { role: "engineer", seatsPerProject: 2 },
  { role: "intern", seatsPerProject: 0.5 },
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
  const sharedSpecialists = SHARED_SPECIALIST.reduce((n, r) => n + r.seats, 0);
  const sharedPool = SHARED_ENGINEERING_POOL.reduce(
    (n, r) => n + Math.ceil(r.seatsPerProject * projectCount),
    0,
  );
  const shared = SHARED_EXECUTIVE.length + sharedSpecialists + sharedPool + AUDIT.length;
  return perProject * projectCount + shared;
}
