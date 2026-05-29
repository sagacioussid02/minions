/**
 * Parse + validate operator-facing agent_ids (Surface B / B3).
 *
 * agent_id format: ``<role>@<scope>[#<seat>]``
 *   - role: stable string from minions/models/roles.py (e.g. "engineer", "ceo")
 *   - scope: project name or literal "org" for shared/exec seats
 *   - seat: optional integer suffix for multi-seat roles (seat 0 has no suffix)
 *
 * The TypeScript side does not own the canonical roster — the Python crews do.
 * This module performs *shape* validation and provides display-name lookup so
 * the chat persona reads naturally. Anything beyond that (capacity, eligibility,
 * etc.) is out of scope; the API trusts that the UI only renders valid cards.
 */

const AGENT_ID_RE = /^([a-z_]+)@([A-Za-z0-9_-]+)(?:#(\d+))?$/;

export type ParsedAgentId = {
  agentId: string;
  role: string;
  /** null when scope === "org" (shared / exec seats). */
  project: string | null;
  seat: number;
};

export function parseAgentId(raw: string): ParsedAgentId | null {
  const m = AGENT_ID_RE.exec(raw);
  if (!m) return null;
  const [, role, scope, seatStr] = m;
  return {
    agentId: raw,
    role,
    project: scope === "org" ? null : scope,
    seat: seatStr ? Number.parseInt(seatStr, 10) : 0,
  };
}

/** Title-cased role fallback used when no display name is on file. */
export function fallbackDisplayName(role: string): string {
  return role
    .split("_")
    .map((part) => (part.length === 0 ? part : part[0].toUpperCase() + part.slice(1)))
    .join(" ");
}
