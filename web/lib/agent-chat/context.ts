/**
 * Chat context bundler — TS twin of ``minions.agent_chat.context``.
 *
 * Composes the persona + dossier + learning + transcripts that drive a single
 * agent's reply, with a hard UTF-8 byte budget. The Python side is canonical;
 * this file mirrors its rules so traces from either runtime look the same.
 */

import {
  latestMergedDossierMarkdown,
  listLearningForAgent,
  listRecentTranscripts,
  lookupDisplayName,
  type LearningSnippet,
  type TranscriptSnippet,
} from "./repo";
import { fallbackDisplayName, type ParsedAgentId } from "./roster";
import { getAgentProfile, listActiveAgents } from "../queries";
import { type AgentProfile, type AgentState } from "../schemas";

export const MAX_PROMPT_BYTES = 8 * 1024;
export const MAX_DOSSIER_BYTES = 2 * 1024;
export const MAX_LEARNING_RECORDS = 15;
export const MAX_TRANSCRIPT_SNIPPETS = 5;
export const MAX_TEAM_MEMBERS = 24;
const TRANSCRIPT_SNIPPET_CHARS = 400;

/** A teammate the agent should be aware of (project peers + leadership). */
export type TeamMember = {
  displayName: string;
  role: string;
  /** null for shared/leadership seats. */
  project: string | null;
  tier: AgentState["role_tier"];
  leadership: boolean;
  inFlight: boolean;
};

// Mirrors src/minions/agents/safety.py — keep verbatim. If the Python preamble
// changes, update here too; the operator-facing rules must reach the model
// regardless of which runtime placed the call.
const SAFETY_PREAMBLE = `# Hard Rules (non-negotiable)

1. You MUST NOT read .env files or any secret material. The filesystem will
   deny such reads — do not try to circumvent it. Reference secrets by name
   only (e.g., \${ANTHROPIC_API_KEY}); never inline a secret value.

2. You MUST NOT push commits to the \`main\` or \`master\` branch. Always create
   a branch named \`minions/<role>/<short-summary>\`, commit there, and open a
   PR targeting main. Branch protection enforces this server-side; do not
   request a bypass.

3. Every change you produce goes through code review. A peer agent reviews
   first. Only after peer approval and green CI does the operator review.
   Do not merge your own work.

4. Every material decision (feature, bug fix, dependency upgrade, infra
   change, security patch, license/cert renewal, cost change, procurement,
   team-composition change) is proposed via a Decision Record. The operator
   approves before execution. The agent proposes; the operator disposes.

5. You MUST NOT accept Terms of Service on the operator's behalf unless the
   operator has explicitly authorized TOS acceptance for that specific
   vendor in writing (recorded in the audit log).

If a tool returns a permission denied error, accept it as final. Do not retry
with a different path or escalation. Surface the attempt as a security alert
in your response.`;

export type ChatContext = {
  agentId: string;
  role: string;
  displayName: string;
  project: string | null;
  persona: string;
  dossierExcerpt: string;
  learning: LearningSnippet[];
  transcriptSnippets: TranscriptSnippet[];
  teammates: TeamMember[];
  coldStart: boolean;
  totalBytes: number;
};

export async function buildAgentContext(parsed: ParsedAgentId): Promise<ChatContext> {
  const { agentId, role, project } = parsed;

  const [displayName, dossierMd, learning, transcripts, profile, roster] =
    await Promise.all([
      lookupDisplayName({ project, role }).then(
        (name) => name ?? fallbackDisplayName(role),
      ),
      project ? latestMergedDossierMarkdown(project) : Promise.resolve(null),
      listLearningForAgent(agentId, MAX_LEARNING_RECORDS),
      listRecentTranscripts(project, MAX_TRANSCRIPT_SNIPPETS),
      getAgentProfile(agentId).catch(() => null),
      // The roster the operator console renders — already tenant-scoped. We
      // filter it down to this agent's project peers + leadership so the agent
      // knows who they work with. Best-effort: never block a reply on it.
      listActiveAgents().catch(() => [] as AgentState[]),
    ]);

  const dossierExcerpt = dossierMd ? truncateUtf8(dossierMd, MAX_DOSSIER_BYTES) : "";
  const persona = renderPersona({ role, displayName, project, profile });
  const teammates = selectTeammates(roster, { role, project });
  const coldStart = learning.length === 0 && transcripts.length === 0;

  const ctx: ChatContext = {
    agentId,
    role,
    displayName,
    project,
    persona,
    dossierExcerpt,
    learning,
    transcriptSnippets: transcripts,
    teammates,
    coldStart,
    totalBytes: 0,
  };
  enforceBudget(ctx);
  return ctx;
}

/**
 * Project team + leadership. An agent knows the peers assigned to their own
 * project, plus the executive/leadership seats. The agent themself is excluded.
 *
 * Leadership is the executive role tier only (ceo/cto/managing_director/
 * org_owner) — NOT merely "null project", which also covers shared bench/legacy
 * seats that aren't actually leadership.
 */
function selectTeammates(
  roster: AgentState[],
  self: { role: string; project: string | null },
): TeamMember[] {
  const members: TeamMember[] = [];
  for (const a of roster) {
    const isSelf = a.role === self.role && a.project === self.project;
    if (isSelf) continue;
    const leadership = a.role_tier === "executive";
    const sameProject = self.project !== null && a.project === self.project;
    if (!sameProject && !leadership) continue;
    members.push({
      displayName: a.display_name?.trim() || fallbackDisplayName(a.role),
      role: a.role,
      project: a.project,
      tier: a.role_tier,
      leadership,
      inFlight: a.in_flight,
    });
  }
  // Leadership first, then project peers; stable by name within each group.
  members.sort((x, y) =>
    x.leadership === y.leadership
      ? x.displayName.localeCompare(y.displayName)
      : x.leadership
        ? -1
        : 1,
  );
  return members.slice(0, MAX_TEAM_MEMBERS);
}

function renderPersona({
  role,
  displayName,
  project,
  profile,
}: {
  role: string;
  displayName: string;
  project: string | null;
  profile?: AgentProfile | null;
}): string {
  const projectLine = project ? `You are working in project '${project}'.\n` : "";
  const creds = profile ? profileCredentials(profile) : "";
  const trackRecord = creds ? `Your track record: ${creds}.\n\n` : "";
  return (
    `Your operator-facing name is ${displayName}. Speak in first person.\n\n` +
    `${projectLine}` +
    `You are an agent with role '${role}' in the minions organization.\n\n` +
    `${trackRecord}` +
    SAFETY_PREAMBLE
  );
}

// TS twin of minions.agents.recall.identity_credentials — keep in sync so chat
// describes the agent the same way the crews do.
function profileCredentials(p: AgentProfile): string {
  const bits: string[] = [];
  if (p.joined_sprint != null) bits.push(`since sprint ${p.joined_sprint}`);
  if (p.stats.prs_merged || p.stats.prs_opened) {
    bits.push(`${p.stats.prs_merged} merged of ${p.stats.prs_opened} PRs`);
  }
  if (p.stats.reviews_received) bits.push(`${p.stats.reviews_received} reviews`);
  if (p.specialties.length > 0) bits.push(`strong on ${p.specialties.join(", ")}`);
  return bits.join("; ");
}

function truncateUtf8(text: string, maxBytes: number): string {
  const encoded = new TextEncoder().encode(text);
  if (encoded.length <= maxBytes) return text;
  const slice = encoded.slice(0, maxBytes);
  return new TextDecoder("utf-8", { fatal: false }).decode(slice);
}

function snippetText(t: TranscriptSnippet): string {
  return `[${t.crew}/${t.agent_role}] ${t.content.slice(0, TRANSCRIPT_SNIPPET_CHARS)}`;
}

export function teamMemberLine(m: TeamMember): string {
  const where = m.leadership
    ? "leadership"
    : m.project
      ? `project ${m.project}`
      : "org";
  return `- ${m.displayName} — ${m.role} (${where})${m.inFlight ? " · active now" : ""}`;
}

function bundleBytes(ctx: ChatContext): number {
  const encoder = new TextEncoder();
  let total = 0;
  total += encoder.encode(ctx.persona).length;
  total += encoder.encode(ctx.dossierExcerpt).length;
  for (const r of ctx.learning) total += encoder.encode(r.fact).length;
  for (const t of ctx.transcriptSnippets) total += encoder.encode(snippetText(t)).length;
  for (const m of ctx.teammates) total += encoder.encode(teamMemberLine(m)).length;
  return total;
}

function enforceBudget(ctx: ChatContext): void {
  // Pass 1: shrink the dossier.
  while (bundleBytes(ctx) > MAX_PROMPT_BYTES && ctx.dossierExcerpt) {
    const current = new TextEncoder().encode(ctx.dossierExcerpt).length;
    const newLen = Math.max(0, Math.floor(current / 2));
    ctx.dossierExcerpt = newLen === 0 ? "" : truncateUtf8(ctx.dossierExcerpt, newLen);
    if (newLen === 0) break;
  }
  // Pass 2: drop learning records from the tail.
  while (bundleBytes(ctx) > MAX_PROMPT_BYTES && ctx.learning.length > 0) {
    ctx.learning.pop();
  }
  // Pass 3: drop transcript snippets.
  while (bundleBytes(ctx) > MAX_PROMPT_BYTES && ctx.transcriptSnippets.length > 0) {
    ctx.transcriptSnippets.pop();
  }
  // Pass 4: trim the team roster from the tail (project peers go before
  // leadership, since the list is sorted leadership-first).
  while (bundleBytes(ctx) > MAX_PROMPT_BYTES && ctx.teammates.length > 0) {
    ctx.teammates.pop();
  }
  ctx.totalBytes = bundleBytes(ctx);
}
