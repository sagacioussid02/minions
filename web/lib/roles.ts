/**
 * Role taxonomy — must stay in sync with `src/minions/models/roles.py` and
 * `src/minions/agents/roster.py`.
 *
 * The Floor uses ``tierFor()`` to decide which row a card sits in
 * (executive / engineering / audit / specialist) and which icon shape to use.
 */

export type RoleTier = "executive" | "engineering" | "audit" | "specialist";

const TIER_BY_ROLE: Record<string, RoleTier> = {
  // Executives
  ceo: "executive",
  cto: "executive",
  managing_director: "executive",
  org_owner: "executive",
  // Engineering line
  product_owner: "engineering",
  manager: "engineering",
  principal_engineer: "engineering",
  tech_team_lead: "engineering",
  senior_engineer: "engineering",
  engineer: "engineering",
  intern: "engineering",
  // Audit + Security
  chief_auditor: "audit",
  process_auditor: "audit",
  code_auditor: "audit",
  cost_auditor: "audit",
  devils_advocate: "audit",
  qa_engineer: "audit",
  security_champion: "audit",
  test_architect: "audit",
  // Shared specialists
  cloud_devops: "specialist",
  devsecops: "specialist",
  team_architect: "specialist",
  senior_devops: "specialist",
  performance_engineer: "specialist",
  data_engineer: "specialist",
  documentation_engineer: "specialist",
};

export function tierFor(role: string): RoleTier {
  return TIER_BY_ROLE[role.toLowerCase()] ?? "engineering";
}

export function iconFor(tier: RoleTier): string {
  switch (tier) {
    case "executive":
      return "◆";
    case "audit":
      return "▲";
    case "specialist":
      return "★";
    default:
      return "●";
  }
}

export function prettyRole(role: string): string {
  return role
    .split("_")
    .map((p) => p[0]?.toUpperCase() + p.slice(1))
    .join(" ");
}

export function agentSeedFor(role: string | null, project: string | null): string {
  return `${role ?? "system"}@${project ?? "shared"}`;
}
