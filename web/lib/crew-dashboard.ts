/**
 * Crew x projects dashboard data (crew-plans-and-slots 2d).
 *
 * Everything here is a read of tables the engine already writes: active charters
 * (goal), agent_profiles (who exists, expertise), issue_mirror (the work queue) and
 * slot_ledger (this ISO week's usage). Every query filters on tenant_id.
 */

import { sql } from "./db";
import { getTenantId } from "./tenant";

export type CrewAgent = {
  agent_id: string;
  role: string;
  name: string;
  expertise: string[];
  current_issue: { number: number; title: string; url: string } | null;
  slots_used: number;
};

export type CrewProject = {
  project: string;
  goal: string | null;
  product_owner: string | null;
  queue: { needs_approval: number; ready: number; in_progress: number; blocked: number };
  agents: CrewAgent[];
};

export type CrewDashboard = {
  projects: CrewProject[];
  shared: CrewAgent[];
  slots_used_this_week: number;
};

/** Mirrors `crew_label()` in the engine: the label that names the owning agent. */
export function crewSlug(name: string): string {
  return name.trim().toLowerCase().replace(/ /g, "-");
}

type Profile = {
  agent_id: string;
  role: string;
  display_name: string | null;
  specialties: unknown;
};
type MirrorRow = {
  project: string;
  number: number;
  state: string;
  queue_status: string;
  title: string | null;
  url: string | null;
  labels: unknown;
};
type LedgerRow = { project: string; issue_number: number; units: number; labels: unknown };

function strings(v: unknown): string[] {
  return Array.isArray(v) ? v.map(String) : [];
}

export async function loadCrewDashboard(): Promise<CrewDashboard> {
  const db = sql();
  const tid = await getTenantId();

  const [profiles, charters, issues, ledger] = await Promise.all([
    db`SELECT agent_id, role, display_name, specialties
       FROM agent_profiles WHERE tenant_id = ${tid} ORDER BY role, agent_id`,
    db`SELECT DISTINCT ON (project) project, payload->'body'->>'goal' AS goal
       FROM project_charters WHERE tenant_id = ${tid} AND status = 'active'
       ORDER BY project, version DESC`,
    db`SELECT project, number, state, queue_status,
              payload->>'title' AS title, payload->>'html_url' AS url,
              payload->'labels' AS labels
       FROM issue_mirror WHERE tenant_id = ${tid} AND state = 'open'
       ORDER BY project, number LIMIT 2000`,
    // Slot units charged this ISO week (Monday, UTC), with the issue's labels so usage
    // can be attributed to the crew:<name> agent that owns it.
    db`SELECT l.project, l.issue_number, l.units, m.payload->'labels' AS labels
       FROM slot_ledger l
       LEFT JOIN issue_mirror m
         ON m.tenant_id = l.tenant_id AND m.project = l.project AND m.number = l.issue_number
       WHERE l.tenant_id = ${tid}
         AND l.week_start = date_trunc('week', now() AT TIME ZONE 'UTC')::date`,
  ]);

  const goalBy = new Map(
    (charters as { project: string; goal: string | null }[]).map((c) => [c.project, c.goal]),
  );
  const openIssues = issues as MirrorRow[];
  const ledgerRows = ledger as LedgerRow[];

  const toAgent = (p: Profile, project: string | null): CrewAgent => {
    const slug = p.display_name ? crewSlug(p.display_name) : null;
    const owns = (labels: unknown) => slug !== null && strings(labels).includes(`crew:${slug}`);
    const current = openIssues.find(
      (i) =>
        (project === null || i.project === project) &&
        i.queue_status === "in_progress" &&
        owns(i.labels),
    );
    return {
      agent_id: p.agent_id,
      role: p.role,
      name: p.display_name ?? p.agent_id,
      expertise: strings(p.specialties),
      current_issue: current
        ? { number: current.number, title: current.title ?? `#${current.number}`, url: current.url ?? "" }
        : null,
      slots_used: ledgerRows
        .filter((l) => (project === null || l.project === project) && owns(l.labels))
        .reduce((n, l) => n + l.units, 0),
    };
  };

  const byProject = new Map<string, Profile[]>();
  const sharedProfiles: Profile[] = [];
  for (const p of profiles as Profile[]) {
    const scope = p.agent_id.split("@")[1];
    if (!scope || scope === "shared") sharedProfiles.push(p);
    else byProject.set(scope, [...(byProject.get(scope) ?? []), p]);
  }

  const names = new Set<string>([
    ...goalBy.keys(),
    ...byProject.keys(),
    ...openIssues.map((i) => i.project),
  ]);
  const projects: CrewProject[] = [...names].sort().map((project) => {
    const agents = (byProject.get(project) ?? []).map((p) => toAgent(p, project));
    const queue = { needs_approval: 0, ready: 0, in_progress: 0, blocked: 0 };
    for (const i of openIssues) {
      if (i.project === project && i.queue_status in queue) {
        queue[i.queue_status as keyof typeof queue] += 1;
      }
    }
    return {
      project,
      goal: goalBy.get(project) ?? null,
      product_owner: agents.find((a) => a.role === "product_owner")?.name ?? null,
      queue,
      agents,
    };
  });

  return {
    projects,
    shared: sharedProfiles.map((p) => toAgent(p, null)),
    slots_used_this_week: ledgerRows.reduce((n, l) => n + l.units, 0),
  };
}
