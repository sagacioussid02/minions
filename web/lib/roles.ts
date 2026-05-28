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

// Short, human role labels for chips shown under an agent's name. Anything
// not listed falls back to prettyRole(). Keep these tight — they sit as a
// secondary line beneath the name.
const ROLE_SHORT_LABEL: Record<string, string> = {
  ceo: "CEO",
  cto: "CTO",
  managing_director: "MD",
  org_owner: "Org Owner",
  product_owner: "Product Owner",
  manager: "Manager",
  principal_engineer: "Principal Engg",
  tech_team_lead: "Tech Lead",
  senior_engineer: "Senior Engg",
  engineer: "Engineer",
  intern: "Intern",
  chief_auditor: "Chief Auditor",
  process_auditor: "Process Auditor",
  code_auditor: "Code Auditor",
  cost_auditor: "Cost Auditor",
  devils_advocate: "Devil's Advocate",
  qa_engineer: "QA Engg",
  security_champion: "Security",
  test_architect: "Test Architect",
  cloud_devops: "Cloud DevOps",
  devsecops: "DevSecOps",
  team_architect: "Architect",
  senior_devops: "Senior DevOps",
  performance_engineer: "Perf Engg",
  data_engineer: "Data Engg",
  documentation_engineer: "Docs Engg",
};

export function roleShortLabel(role: string): string {
  return ROLE_SHORT_LABEL[role.toLowerCase()] ?? prettyRole(role);
}

/**
 * One-line agent label, name-first: ``"Vera — Senior Engg"``. Falls back to
 * the short role alone when no display name is known. Used wherever an agent
 * is referenced inline (lists, single-line contexts).
 */
export function agentLabel(
  displayName: string | null | undefined,
  role: string,
): string {
  const short = roleShortLabel(role);
  const name = (displayName ?? "").trim();
  if (!name) return short;
  return `${name} — ${short}`;
}

export function agentSeedFor(role: string | null, project: string | null): string {
  return `${role ?? "system"}@${project ?? "shared"}`;
}
