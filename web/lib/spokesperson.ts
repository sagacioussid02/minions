import { sql } from "@/lib/db";

export const SPOKESPERSON_ROLES = [
  "ceo",
  "cto",
  "chief_product_officer",
  "coo",
  "managing_director",
  "portfolio_owner",
  "product_manager",
  "security_champion",
  "spokesperson",
] as const;

type QuestionKind =
  | "repo_inventory"
  | "technical"
  | "functional"
  | "deployment"
  | "security"
  | "cost"
  | "portfolio"
  | "generic";

type Citation = {
  source_type: string;
  label: string;
  reference: string | null;
  excerpt: string;
};

type InterviewThread = {
  id: string;
  scope: "project" | "organization";
  project: string | null;
  spokesperson_role: string;
  title: string;
  created_at: string;
  updated_at: string;
};

const MANAGED_REPOS: Array<{
  project: string;
  repo: string | null;
  status: "active" | "deferred";
  note: string;
}> = [
  { project: "AaaG", repo: "sagacioussid02/aaag", status: "active", note: "GitHub managed project" },
  { project: "quantumanic", repo: "sagacioussid02/qxplore", status: "active", note: "GitHub managed project" },
  { project: "twin", repo: "sagacioussid02/personas", status: "active", note: "GitHub managed project" },
  { project: "sidspace", repo: "sagacioussid02/sidspace", status: "active", note: "GitHub managed project" },
  { project: "sonicrochet", repo: "sagacioussid02/sonicrochet", status: "active", note: "GitHub managed project" },
  { project: "trading", repo: null, status: "deferred", note: "Local/deferred project, not an active GitHub repo in the public console" },
];

function nowIso(): string {
  return new Date().toISOString();
}

function normalizeRole(role: string): string {
  return role.trim().toLowerCase().replaceAll(" ", "_").replaceAll("-", "_");
}

function redactSecrets(text: string): string {
  return text
    .replace(/sk-ant-[A-Za-z0-9_-]{16,}/g, "<redacted-secret>")
    .replace(/sk-[A-Za-z0-9_-]{16,}/g, "<redacted-secret>")
    .replace(/gh[pousr]_[A-Za-z0-9_]{20,}/g, "<redacted-secret>")
    .replace(
      /(api[_-]?key|token|password|secret)\s*[:=]\s*['"]?[^'"\s]{8,}/gi,
      (_match, key) => `${key}=<redacted>`,
    );
}

/**
 * Normalize a question for the question-memory lookup: lowercase, collapse
 * whitespace, drop trailing punctuation. Intentionally simple (exact match
 * after normalization) — semantic match via embeddings is a follow-up.
 */
function normalizeQuestionKey(question: string): string {
  return question
    .toLowerCase()
    .replace(/\s+/g, " ")
    .replace(/[\s.?!,;:]+$/g, "")
    .trim();
}

interface PriorAnswer {
  asked_at: string;
  prior_answer: string | null;
  spike_decision_id: string | null;
  spike_status: string | null;
  spike_pr_url: string | null;
}

/**
 * Look for a prior operator question identical to ``question`` (after
 * normalization) in either the current thread or anywhere in the
 * spokesperson's history. If found, also resolve the SPIKE Decision that
 * was spawned from it (if any) and whether the answer ever came back.
 */
async function findPriorAnswer(
  question: string,
  threadId: string | null,
  spokespersonRole: string,
): Promise<PriorAnswer | null> {
  const s = sql();
  const normalized = normalizeQuestionKey(question);
  if (!normalized) return null;
  // Look at operator messages — those are the questions. Match within the
  // current thread first (highest signal). Fall back to any thread for the
  // same spokesperson role. Pick the most recent prior occurrence.
  const rows = (await s`
    SELECT m.id::text AS message_id,
           m.thread_id::text AS thread_id,
           m.created_at,
           m.payload->>'content' AS content
    FROM interview_messages m
    JOIN interview_threads t ON t.id = m.thread_id
    WHERE m.role = 'operator'
      AND t.spokesperson_role = ${spokespersonRole}
      AND LOWER(BTRIM(COALESCE(m.payload->>'content', ''))) LIKE ${`%${normalized}%`}
      AND m.id::text <> COALESCE(${threadId}::text, '')
    ORDER BY (m.thread_id = ${threadId}::uuid) DESC NULLS LAST, m.created_at DESC
    LIMIT 5
  `) as Array<{
    message_id: string;
    thread_id: string;
    created_at: string;
    content: string | null;
  }>;
  const hit = rows.find(
    (r) => normalizeQuestionKey(r.content ?? "") === normalized,
  );
  if (!hit) return null;

  // Find the most recent spokesperson reply in the same thread that came
  // after the matched operator message — that's the prior answer.
  const replies = (await s`
    SELECT m.payload->>'content' AS content
    FROM interview_messages m
    WHERE m.thread_id = ${hit.thread_id}::uuid
      AND m.role = 'spokesperson'
      AND m.created_at >= ${hit.created_at}::timestamptz
    ORDER BY m.created_at ASC
    LIMIT 1
  `) as Array<{ content: string | null }>;
  const priorAnswer = replies[0]?.content ?? null;

  // Did that question spawn a SPIKE? If so, look up its current state.
  const spikes = (await s`
    SELECT id::text AS id,
           status,
           payload->>'pr_url' AS pr_url
    FROM decisions
    WHERE payload->>'spike_source' = 'spokesperson_interview'
      AND payload->>'thread_id' = ${hit.thread_id}
      AND payload->>'message_id' = ${hit.message_id}
    ORDER BY created_at DESC
    LIMIT 1
  `) as Array<{ id: string; status: string; pr_url: string | null }>;

  return {
    asked_at: hit.created_at,
    prior_answer: priorAnswer,
    spike_decision_id: spikes[0]?.id ?? null,
    spike_status: spikes[0]?.status ?? null,
    spike_pr_url: spikes[0]?.pr_url ?? null,
  };
}

function priorAnswerPreface(prior: PriorAnswer): string {
  const date = new Date(prior.asked_at).toISOString().slice(0, 10);
  const lines: string[] = [
    `**Asked before on ${date}.**`,
  ];
  if (prior.prior_answer) {
    const snippet = prior.prior_answer.length > 280
      ? `${prior.prior_answer.slice(0, 277)}…`
      : prior.prior_answer;
    lines.push(`Previous answer: ${snippet}`);
  }
  if (prior.spike_decision_id) {
    const shortId = prior.spike_decision_id.slice(0, 8);
    if (prior.spike_pr_url) {
      lines.push(
        `A SPIKE was opened (\`${shortId}\`, status: ${prior.spike_status ?? "unknown"}) ` +
        `and the engineer crew posted findings in ${prior.spike_pr_url}.`,
      );
    } else {
      lines.push(
        `A SPIKE was opened (\`${shortId}\`, status: ${prior.spike_status ?? "unknown"}). ` +
        `No PR yet.`,
      );
    }
  }
  return lines.join("\n\n");
}

function inferProjectFromQuestion(question: string, projects: string[]): string | null {
  const sorted = [...projects]
    .filter((project) => project.trim().length > 1)
    .sort((a, b) => b.length - a.length);
  for (const project of sorted) {
    const escaped = project.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const pattern = new RegExp(`(^|[^a-z0-9])${escaped}([^a-z0-9]|$)`, "i");
    if (pattern.test(question)) return project;
  }
  return null;
}

function isOrganizationWideQuestion(question: string, kind: QuestionKind): boolean {
  if (kind === "repo_inventory" || kind === "portfolio" || kind === "cost") return true;
  return /\b(all|every|portfolio|org|organization|minions org|across projects|all projects)\b/i.test(question);
}

export function classifyQuestion(question: string): QuestionKind {
  const q = question.toLowerCase();
  if (/(repo|repos|repository|repositories|github).*(owned|managed|list|all|minions org|org)|list.*(repo|repos|repository|repositories)/.test(q)) {
    return "repo_inventory";
  }
  if (/deploy|deployment|hosting|hosted|runtime|infra|server|cloud/.test(q)) return "deployment";
  if (/secret|password|token|api key|api_key|rotate|rotation|vulnerability|security/.test(q)) return "security";
  if (/cost|spend|budget|burn|expensive|usage/.test(q)) return "cost";
  if (/architecture|code|stack|database|api|framework|library|technical/.test(q)) return "technical";
  if (/roadmap|feature|user|workflow|sprint|demo|status|requirement/.test(q)) return "functional";
  if (/portfolio|investor|strategy|priority|staffing|team/.test(q)) return "portfolio";
  return "generic";
}

export function routeRoles(kind: QuestionKind, spokespersonRole: string): string[] {
  const routes: Record<QuestionKind, string[]> = {
    repo_inventory: ["cto", "portfolio_owner"],
    functional: ["product_manager", "manager"],
    technical: ["principal_engineer", "team_architect"],
    deployment: ["product_manager", "cloud_devops", "principal_engineer"],
    security: ["security_champion", "devsecops"],
    cost: ["cost_auditor", "cto", "managing_director"],
    portfolio: ["ceo", "cto", "managing_director", "portfolio_owner"],
    generic: ["product_manager", "manager"],
  };
  const executiveDelegates: Record<string, string[]> = {
    ceo: ["cto", "chief_product_officer", "managing_director"],
    chief_product_officer: ["product_manager", "portfolio_owner"],
    coo: ["manager", "principal_engineer"],
  };
  const frontDoor = spokespersonRole === "spokesperson" ? [] : [spokespersonRole];
  const executiveRoute = executiveDelegates[normalizeRole(spokespersonRole)] ?? [];
  return [...frontDoor, ...executiveRoute, ...routes[kind]].reduce<string[]>((acc, role) => {
    const normalized = normalizeRole(role);
    if (!acc.includes(normalized)) acc.push(normalized);
    return acc;
  }, []);
}

export async function ensureInterviewTables() {
  const s = sql();
  await s`
    CREATE TABLE IF NOT EXISTS interview_threads (
      id uuid PRIMARY KEY,
      scope text NOT NULL,
      project text,
      spokesperson_role text NOT NULL,
      created_at timestamptz NOT NULL,
      updated_at timestamptz NOT NULL,
      payload jsonb NOT NULL
    )
  `;
  await s`
    CREATE TABLE IF NOT EXISTS interview_messages (
      id uuid PRIMARY KEY,
      thread_id uuid NOT NULL,
      role text NOT NULL,
      agent_role text,
      created_at timestamptz NOT NULL,
      payload jsonb NOT NULL
    )
  `;
  await s`
    CREATE TABLE IF NOT EXISTS interview_consultations (
      id uuid PRIMARY KEY,
      thread_id uuid NOT NULL,
      message_id uuid NOT NULL,
      project text,
      consulted_role text NOT NULL,
      status text NOT NULL,
      created_at timestamptz NOT NULL,
      updated_at timestamptz NOT NULL,
      payload jsonb NOT NULL
    )
  `;
  await s`
    CREATE TABLE IF NOT EXISTS interview_task_proposals (
      id uuid PRIMARY KEY,
      thread_id uuid NOT NULL,
      message_id uuid NOT NULL,
      project text,
      owner_role text NOT NULL,
      status text NOT NULL,
      created_at timestamptz NOT NULL,
      payload jsonb NOT NULL
    )
  `;
}

export async function listSpokespersonProjects(): Promise<string[]> {
  const s = sql();
  const rows = (await s`
    SELECT DISTINCT project
    FROM (
      SELECT project FROM decisions WHERE project IS NOT NULL AND project <> ''
      UNION ALL
      SELECT project FROM activity_log WHERE project IS NOT NULL AND project <> ''
      UNION ALL
      SELECT project FROM cost_log WHERE project IS NOT NULL AND project <> ''
    ) p
    ORDER BY project ASC
  `) as Array<{ project: string }>;
  const databaseProjects = rows.map((r) => r.project);
  return Array.from(
    new Set([
      ...databaseProjects,
      ...MANAGED_REPOS.filter((repo) => repo.status === "active").map((repo) => repo.project),
    ]),
  ).sort((a, b) => a.localeCompare(b));
}

export async function listInterviewThreads(project?: string | null) {
  await ensureInterviewTables();
  const s = sql();
  const rows = project
    ? ((await s`
        SELECT payload
        FROM interview_threads
        WHERE project = ${project}
        ORDER BY updated_at DESC
      `) as Array<{ payload: Record<string, unknown> }>)
    : ((await s`
        SELECT payload
        FROM interview_threads
        ORDER BY updated_at DESC
      `) as Array<{ payload: Record<string, unknown> }>);
  return rows.map((r) => r.payload);
}

export async function createInterviewThread(args: {
  spokesperson_role: string;
  project?: string | null;
  title?: string | null;
}) {
  await ensureInterviewTables();
  const project = args.project?.trim() || null;
  const spokespersonRole = normalizeRole(args.spokesperson_role || "cto");
  const at = nowIso();
  const thread: InterviewThread = {
    id: crypto.randomUUID(),
    scope: project ? "project" : "organization",
    project,
    spokesperson_role: spokespersonRole,
    title: redactSecrets(args.title?.trim() || "Spokesperson interview"),
    created_at: at,
    updated_at: at,
  };
  await upsertThread(thread);
  return thread;
}

export async function getInterviewBundle(threadId: string) {
  await ensureInterviewTables();
  const s = sql();
  const threads = (await s`
    SELECT payload
    FROM interview_threads
    WHERE id = ${threadId}::uuid
    LIMIT 1
  `) as Array<{ payload: Record<string, unknown> }>;
  if (threads.length === 0) return null;
  const messages = (await s`
    SELECT payload
    FROM interview_messages
    WHERE thread_id = ${threadId}::uuid
    ORDER BY created_at ASC
  `) as Array<{ payload: Record<string, unknown> }>;
  const consultations = (await s`
    SELECT payload
    FROM interview_consultations
    WHERE thread_id = ${threadId}::uuid
    ORDER BY created_at ASC
  `) as Array<{ payload: Record<string, unknown> }>;
  const tasks = (await s`
    SELECT payload
    FROM interview_task_proposals
    WHERE thread_id = ${threadId}::uuid
    ORDER BY created_at DESC
  `) as Array<{ payload: Record<string, unknown> }>;
  return {
    thread: threads[0].payload,
    messages: messages.map((r) => r.payload),
    consultations: consultations.map((r) => r.payload),
    tasks: tasks.map((r) => r.payload),
  };
}

export async function askSpokesperson(args: {
  spokesperson_role: string;
  question: string;
  project?: string | null;
  thread_id?: string | null;
}) {
  await ensureInterviewTables();
  const s = sql();
  const question = redactSecrets(args.question.trim());
  const selectedProject = args.project?.trim() || null;
  const knownProjects = await listSpokespersonProjects();
  const mentionedProject = inferProjectFromQuestion(question, knownProjects);
  const spokespersonRole = normalizeRole(args.spokesperson_role || "cto");
  const kind = classifyQuestion(question);
  const orgWide = isOrganizationWideQuestion(question, kind);
  const project = orgWide ? null : mentionedProject ?? selectedProject;
  const scope: InterviewThread["scope"] = project ? "project" : "organization";
  const consultedRoles = routeRoles(kind, spokespersonRole);
  const at = nowIso();
  let thread: InterviewThread = args.thread_id
    ? await getExistingThread(args.thread_id)
    : {
        id: crypto.randomUUID(),
        scope,
        project,
        spokesperson_role: spokespersonRole,
        title: question.replace(/\s+/g, " ").slice(0, 80) || "Spokesperson interview",
        created_at: at,
        updated_at: at,
      };
  if (orgWide && thread.scope !== "organization") {
    thread = {
      ...thread,
      scope: "organization",
      project: null,
      title: question.replace(/\s+/g, " ").slice(0, 80) || "Spokesperson interview",
    };
  } else if (mentionedProject && thread.project !== mentionedProject) {
    thread = {
      ...thread,
      scope: "project",
      project: mentionedProject,
      title: question.replace(/\s+/g, " ").slice(0, 80) || "Spokesperson interview",
    };
  }

  const operatorMessage = {
    id: crypto.randomUUID(),
    thread_id: thread.id,
    role: "operator",
    agent_role: "operator",
    content: question,
    citations: [],
    consulted_roles: [],
    confidence: "high",
    follow_up_actions: [],
    task_proposal_id: null,
    created_at: at,
  };
  await upsertThread({ ...thread, updated_at: at });
  await s`
    INSERT INTO interview_messages (id, thread_id, role, agent_role, created_at, payload)
    VALUES (${operatorMessage.id}::uuid, ${thread.id}::uuid, 'operator', 'operator', NOW(), ${JSON.stringify(operatorMessage)}::jsonb)
  `;

  const projectEvidence = await buildEvidence(project);
  const consultations = [];
  for (const role of consultedRoles) {
    const roleMemory = await buildRoleMemory(role, project);
    const needsScan = project && ["technical", "deployment", "security"].includes(kind);
    const scan = needsScan ? await buildCodeScan(project, question) : null;
    const confidence = scan?.citations.length ? "medium" : roleMemory.citations.length ? "medium" : "low";
    const note = redactSecrets(
      [
        `${role} reviewed this ${kind} question.`,
        roleMemory.summary ? `Memory: ${roleMemory.summary}` : null,
        scan?.summary ? `Code scan: ${scan.summary}` : null,
      ].filter(Boolean).join(" "),
    );
    const consultation = {
      id: crypto.randomUUID(),
      thread_id: thread.id,
      message_id: operatorMessage.id,
      project,
      consulted_role: role,
      status: note ? "answered" : "blocked",
      memory_summary: roleMemory.summary || null,
      code_scan_summary: scan?.summary ?? null,
      files_inspected: scan?.files ?? [],
      note,
      citations: [...roleMemory.citations, ...(scan?.citations ?? [])].slice(0, 12),
      confidence,
      created_at: nowIso(),
      updated_at: nowIso(),
    };
    consultations.push(consultation);
    await s`
      INSERT INTO interview_consultations (
        id, thread_id, message_id, project, consulted_role, status, created_at, updated_at, payload
      ) VALUES (
        ${consultation.id}::uuid, ${thread.id}::uuid, ${operatorMessage.id}::uuid,
        ${project}, ${role}, ${consultation.status}, NOW(), NOW(), ${JSON.stringify(consultation)}::jsonb
      )
    `;
    await s`
      INSERT INTO activity_log (ts, event, project, role, decision_id, crew, run_id, error, payload)
      VALUES (
        NOW(), 'consultation_answered', ${project ?? ""}, ${role}, ${operatorMessage.id},
        'leadership_room', ${`consultation-${thread.id}-${role}`}, NULL,
        ${JSON.stringify({
          thread_id: thread.id,
          question,
          kind,
          speaker_role: role,
          requested_by_role: spokespersonRole,
          summary: consultation.note,
          agents: [role],
        })}::jsonb
      )
    `;
  }

  const hasStrongEvidence = consultations.some(
    (c) =>
      hasUsefulMemory(c.memory_summary) ||
      (c.files_inspected.length > 0 && Boolean(c.code_scan_summary)),
  );
  const hasCodeFindings = consultations.some((c) => c.files_inspected.length > 0);
  const confidence = kind === "repo_inventory"
    ? "high"
    : ["technical", "deployment", "security"].includes(kind) && !hasCodeFindings
      ? "low"
      : hasStrongEvidence ? "medium" : "low";
  const followUps = confidence === "low"
    ? [`${needsSpike(kind) ? "SPIKE: " : ""}${ownerForKind(kind)} should answer '${question}' for ${project ?? "portfolio"}`]
    : [];
  const spikeDecision = followUps.length > 0 && project && needsSpike(kind)
    ? await createSpikeDecision({
        project,
        kind,
        question,
        ownerRole: ownerForKind(kind),
        consultedRoles,
        spokespersonRole,
        threadId: thread.id,
        messageId: operatorMessage.id,
      })
    : null;
  const composedAnswer = composeAnswer({
    spokespersonRole,
    project,
    selectedProject,
    mentionedProject,
    kind,
    confidence,
    consultations,
    projectCitations: projectEvidence.citations,
    followUps,
    spikeDecisionId: spikeDecision?.id ?? null,
  });

  // Question-memory layer: if this question has been asked before in the
  // same spokesperson room, surface the prior answer + the status of any
  // SPIKE that came out of it. Best-effort; failure must not break the
  // chat flow. See `findPriorAnswer` for the matching rules.
  let answerText = composedAnswer;
  try {
    const prior = await findPriorAnswer(question, thread.id, spokespersonRole);
    if (prior) {
      answerText = `${priorAnswerPreface(prior)}\n\n---\n\n${composedAnswer}`;
    }
  } catch (err) {
    console.warn("[askSpokesperson] findPriorAnswer failed:", err);
  }

  let task: Record<string, unknown> | null = null;
  if (followUps.length > 0) {
    task = {
      id: crypto.randomUUID(),
      thread_id: thread.id,
      message_id: operatorMessage.id,
      project,
      owner_role: ownerForKind(kind),
      title: followUps[0],
      rationale: `Spokesperson could not answer confidently: ${question}`,
      status: spikeDecision ? "converted" : "pending",
      decision_id: spikeDecision?.id ?? null,
      created_at: nowIso(),
    };
    await s`
      INSERT INTO interview_task_proposals (id, thread_id, message_id, project, owner_role, status, created_at, payload)
      VALUES (
        ${String(task.id)}::uuid, ${thread.id}::uuid, ${operatorMessage.id}::uuid, ${project},
        ${String(task.owner_role)}, 'pending', NOW(), ${JSON.stringify(task)}::jsonb
      )
    `;
  }

  const answerMessage = {
    id: crypto.randomUUID(),
    thread_id: thread.id,
    role: "spokesperson",
    agent_role: spokespersonRole,
    content: redactSecrets(answerText),
    citations: [...projectEvidence.citations, ...consultations.flatMap((c) => c.citations)].slice(0, 16),
    consulted_roles: consultedRoles,
    confidence,
    follow_up_actions: followUps,
    task_proposal_id: task?.id ?? null,
    created_at: nowIso(),
  };
  await s`
    INSERT INTO interview_messages (id, thread_id, role, agent_role, created_at, payload)
    VALUES (${answerMessage.id}::uuid, ${thread.id}::uuid, 'spokesperson', ${spokespersonRole}, NOW(), ${JSON.stringify(answerMessage)}::jsonb)
  `;
  if (task) {
    task.message_id = answerMessage.id;
    await s`
      UPDATE interview_task_proposals
      SET payload = ${JSON.stringify(task)}::jsonb
      WHERE id = ${String(task.id)}::uuid
    `;
  }
  const updatedThread = { ...thread, updated_at: nowIso() };
  await upsertThread(updatedThread);
  await s`
    INSERT INTO activity_log (ts, event, project, role, decision_id, crew, run_id, error, payload)
    VALUES (
      NOW(), 'spokesperson_answered', ${project ?? ""}, ${spokespersonRole}, ${answerMessage.id},
      'leadership_room', ${`spokesperson-${thread.id}`}, NULL,
      ${JSON.stringify({
        agents: [spokespersonRole, ...consultedRoles.filter((role) => role !== spokespersonRole)],
        speaker_role: spokespersonRole,
        question,
        kind,
      })}::jsonb
    )
  `;
  return { thread: updatedThread, operator_message: operatorMessage, answer_message: answerMessage, consultations, task };
}

export async function createInterviewTask(args: {
  thread_id: string;
  message_id?: string | null;
  owner_role: string;
  title: string;
  rationale?: string | null;
}) {
  await ensureInterviewTables();
  const bundle = await getInterviewBundle(args.thread_id);
  if (!bundle) throw new Error(`unknown interview thread ${args.thread_id}`);
  const thread = bundle.thread as InterviewThread;
  const fallbackMessage = [...bundle.messages]
    .reverse()
    .find((message) => typeof message.id === "string");
  const messageId = args.message_id || String(fallbackMessage?.id ?? "");
  if (!messageId) throw new Error("message_id is required when the thread has no messages");
  const s = sql();
  const task = {
    id: crypto.randomUUID(),
    thread_id: thread.id,
    message_id: messageId,
    project: thread.project,
    owner_role: normalizeRole(args.owner_role || "manager"),
    title: redactSecrets(args.title.trim()),
    rationale: redactSecrets(args.rationale?.trim() || "Created from spokesperson console follow-up."),
    status: "pending",
    decision_id: null,
    created_at: nowIso(),
  };
  await s`
    INSERT INTO interview_task_proposals (id, thread_id, message_id, project, owner_role, status, created_at, payload)
    VALUES (
      ${task.id}::uuid, ${thread.id}::uuid, ${messageId}::uuid, ${thread.project},
      ${task.owner_role}, 'pending', NOW(), ${JSON.stringify(task)}::jsonb
    )
  `;
  return task;
}

async function getExistingThread(threadId: string): Promise<InterviewThread> {
  const bundle = await getInterviewBundle(threadId);
  if (!bundle) throw new Error(`unknown interview thread ${threadId}`);
  return bundle.thread as InterviewThread;
}

async function upsertThread(thread: InterviewThread) {
  const s = sql();
  await s`
    INSERT INTO interview_threads (id, scope, project, spokesperson_role, created_at, updated_at, payload)
    VALUES (
      ${thread.id}::uuid, ${thread.scope}, ${thread.project}, ${thread.spokesperson_role},
      ${thread.created_at}::timestamptz, ${thread.updated_at}::timestamptz, ${JSON.stringify(thread)}::jsonb
    )
    ON CONFLICT (id) DO UPDATE SET
      scope = EXCLUDED.scope,
      project = EXCLUDED.project,
      spokesperson_role = EXCLUDED.spokesperson_role,
      updated_at = EXCLUDED.updated_at,
      payload = EXCLUDED.payload
  `;
}

async function buildEvidence(project: string | null): Promise<{ summary: string; citations: Citation[] }> {
  const s = sql();
  const decisions = project
    ? ((await s`
        SELECT
          id::text AS id,
          project,
          status,
          payload->>'summary' AS summary,
          payload->>'diff_or_plan' AS diff_or_plan
        FROM decisions
        WHERE project = ${project}
          AND COALESCE(payload->>'summary', '') NOT LIKE '%[DRY RUN]%'
        ORDER BY created_at DESC
        LIMIT 5
      `) as Array<{
        id: string;
        project: string;
        status: string;
        summary: string | null;
        diff_or_plan: string | null;
      }>)
    : ((await s`
        SELECT
          id::text AS id,
          project,
          status,
          payload->>'summary' AS summary,
          payload->>'diff_or_plan' AS diff_or_plan
        FROM decisions
        WHERE COALESCE(payload->>'summary', '') NOT LIKE '%[DRY RUN]%'
        ORDER BY created_at DESC
        LIMIT 8
      `) as Array<{
        id: string;
        project: string;
        status: string;
        summary: string | null;
        diff_or_plan: string | null;
      }>);
  const activity = project
    ? ((await s`
        SELECT event, crew, run_id
        FROM activity_log
        WHERE project = ${project}
        ORDER BY ts DESC
        LIMIT 8
      `) as Array<{ event: string; crew: string | null; run_id: string | null }>)
    : ((await s`
        SELECT event, crew, run_id
        FROM activity_log
        ORDER BY ts DESC
        LIMIT 8
      `) as Array<{ event: string; crew: string | null; run_id: string | null }>);
  const costs = project
    ? ((await s`
        SELECT project, role, model, cost_usd
        FROM cost_log
        WHERE project = ${project}
        ORDER BY ts DESC
        LIMIT 8
      `) as Array<{ project: string; role: string | null; model: string | null; cost_usd: number }>)
    : ((await s`
        SELECT project, role, model, cost_usd
        FROM cost_log
        ORDER BY ts DESC
        LIMIT 8
      `) as Array<{ project: string; role: string | null; model: string | null; cost_usd: number }>);
  const repoCitations: Citation[] = project
    ? []
    : MANAGED_REPOS.filter((repo) => repo.status === "active").map((repo) => ({
        source_type: "manifest",
        label: `repo:${repo.project}`,
        reference: repo.repo,
        excerpt: `${repo.project}: ${repo.repo ?? repo.note}`,
      }));
  const citations: Citation[] = [
    ...repoCitations,
    ...decisions.map((d) => ({
      source_type: "decision",
      label: `decision:${d.id.slice(0, 8)}`,
      reference: d.id,
      excerpt: [
        `${d.project} ${d.status}: ${d.summary ?? "Decision recorded"}`,
        d.diff_or_plan ? `Plan: ${compactText(d.diff_or_plan, 1800)}` : null,
      ].filter(Boolean).join(". "),
    })),
    ...activity.map((a) => ({
      source_type: "activity",
      label: `activity:${a.event}`,
      reference: a.run_id,
      excerpt: `${a.crew ?? "crew"} ${a.event}`,
    })),
    ...costs.map((c) => ({
      source_type: "cost",
      label: `cost:${c.project ?? "org"}`,
      reference: c.role,
      excerpt: `${c.model ?? "model"} logged $${Number(c.cost_usd ?? 0).toFixed(4)}`,
    })),
  ];
  return {
    summary: citations.slice(0, 6).map((c) => `${c.label}: ${c.excerpt}`).join("; "),
    citations,
  };
}

async function buildRoleMemory(role: string, project: string | null): Promise<{ summary: string; citations: Citation[] }> {
  const s = sql();
  const decisions = project
    ? ((await s`
        SELECT id::text AS id, status, payload->>'summary' AS summary
        FROM decisions
        WHERE project = ${project}
          AND LOWER(COALESCE(payload->>'proposer_role', '')) LIKE ${`%${role}%`}
        ORDER BY created_at DESC
        LIMIT 4
      `) as Array<{ id: string; status: string; summary: string | null }>)
    : ((await s`
        SELECT id::text AS id, status, payload->>'summary' AS summary
        FROM decisions
        WHERE LOWER(COALESCE(payload->>'proposer_role', '')) LIKE ${`%${role}%`}
        ORDER BY created_at DESC
        LIMIT 4
      `) as Array<{ id: string; status: string; summary: string | null }>);
  const activity = project
    ? ((await s`
        SELECT event, crew, run_id
        FROM activity_log
        WHERE project = ${project}
          AND payload->'agents' @> ${JSON.stringify([role])}::jsonb
        ORDER BY ts DESC
        LIMIT 4
      `) as Array<{ event: string; crew: string | null; run_id: string | null }>)
    : ((await s`
        SELECT event, crew, run_id
        FROM activity_log
        WHERE payload->'agents' @> ${JSON.stringify([role])}::jsonb
        ORDER BY ts DESC
        LIMIT 4
      `) as Array<{ event: string; crew: string | null; run_id: string | null }>);
  const previous = project
    ? ((await s`
        SELECT payload
        FROM interview_consultations
        WHERE project = ${project}
          AND consulted_role = ${role}
          AND payload->>'note' IS NOT NULL
        ORDER BY created_at DESC
        LIMIT 2
      `) as Array<{ payload: { id?: string; note?: string } }>)
    : ((await s`
        SELECT payload
        FROM interview_consultations
        WHERE consulted_role = ${role}
          AND payload->>'note' IS NOT NULL
        ORDER BY created_at DESC
        LIMIT 2
      `) as Array<{ payload: { id?: string; note?: string } }>);
  const citations: Citation[] = [
    ...decisions.map((d) => ({
      source_type: "role_memory",
      label: `${role}:decision:${d.id.slice(0, 8)}`,
      reference: d.id,
      excerpt: `Proposed ${d.summary ?? "Decision"} (${d.status})`,
    })),
    ...activity.map((a) => ({
      source_type: "role_memory",
      label: `${role}:activity:${a.event}`,
      reference: a.run_id,
      excerpt: `${a.crew ?? "crew"} ${a.event}`,
    })),
    ...previous.map((p) => ({
      source_type: "role_memory",
      label: `${role}:consultation:${String(p.payload.id ?? "").slice(0, 8)}`,
      reference: String(p.payload.id ?? ""),
      excerpt: String(p.payload.note ?? ""),
    })),
  ];
  if (citations.length === 0) {
    citations.push({
      source_type: "role_memory",
      label: `${role}:memory`,
      reference: null,
      excerpt: "No prior role-specific memory was found for this scope.",
    });
  }
  return {
    summary: redactSecrets(citations.slice(0, 5).map((c) => c.excerpt).join("; ")),
    citations: citations.map((c) => ({ ...c, excerpt: redactSecrets(c.excerpt) })),
  };
}

async function buildCodeScan(project: string, question: string) {
  const s = sql();
  const keywords = question.toLowerCase().split(/\W+/).filter((w) => w.length >= 4).slice(0, 10);
  const isDeploymentQuestion = /deploy|deployment|hosting|hosted|runtime|infra|server|cloud/.test(
    question.toLowerCase(),
  );
  const rows = (await s`
    SELECT pr_url, payload->'files_changed' AS files_changed
    FROM engineer_runs
    WHERE project = ${project}
    ORDER BY completed_at DESC
    LIMIT 10
  `) as Array<{ pr_url: string | null; files_changed: unknown }>;
  const files = rows.flatMap((r) => Array.isArray(r.files_changed) ? r.files_changed.map(String) : []);
  const relevant = files.filter((f) => (
    keywords.some((k) => f.toLowerCase().includes(k)) ||
    /deploy|docker|vercel|fly|render|railway|env|config|secret/i.test(f)
  ));
  const inspected = Array.from(new Set(relevant.length ? relevant : isDeploymentQuestion ? [] : files)).slice(0, 12);
  const citations = inspected.map((file) => ({
    source_type: "code_scan",
    label: file,
    reference: file,
    excerpt: `Recent engineer-run history touched ${file}.`,
  }));
  return {
    summary: inspected.length
      ? `Historical read-only scan inspected recorded changed files: ${inspected.slice(0, 6).join(", ")}.`
      : isDeploymentQuestion
        ? "No recorded deployment/config files were found in recent engineer-run history."
      : "No local checkout is available to the public UI; checked recorded engineer-run file history instead.",
    files: inspected,
    citations,
  };
}

function ownerForKind(kind: QuestionKind): string {
  return {
    repo_inventory: "cto",
    deployment: "cloud_devops",
    technical: "principal_engineer",
    security: "security_champion",
    cost: "cost_auditor",
    portfolio: "cto",
    functional: "manager",
    generic: "product_manager",
  }[kind];
}

function needsSpike(kind: QuestionKind): boolean {
  return ["technical", "deployment", "security"].includes(kind);
}

function hasUsefulMemory(summary: string | null | undefined): boolean {
  if (!summary) return false;
  return !/no prior role-specific memory/i.test(summary);
}

function compactText(text: string, max = 280): string {
  const normalized = text.replace(/\s+/g, " ").trim();
  return normalized.length > max ? `${normalized.slice(0, max - 1).trim()}…` : normalized;
}

function autoApproveInvestigationFor(role: string): boolean {
  return normalizeRole(role) !== "spokesperson";
}

function priorityForLeadershipRole(role: string): "p1" | "p2" | "p3" {
  const normalized = normalizeRole(role);
  if (
    [
      "ceo",
      "cto",
      "md",
      "managing_director",
      "chair",
      "board",
      "chief_product_officer",
      "coo",
    ].includes(normalized)
  ) {
    return "p1";
  }
  if (
    [
      "principal",
      "principal_engineer",
      "pm",
      "product_manager",
      "portfolio_owner",
      "security_champion",
      "spokesperson",
    ].includes(normalized)
  ) {
    return "p2";
  }
  return "p3";
}

async function createSpikeDecision(args: {
  project: string;
  kind: QuestionKind;
  question: string;
  ownerRole: string;
  consultedRoles: string[];
  // Role of the spokesperson room this SPIKE was filed from (cto, ceo, pm, …).
  // Stamped onto the Decision so the Python validator can auto-derive
  // priority/expedited. See `_ROLE_PRIORITY_DEFAULTS` in
  // `minions/src/minions/models/decision.py` — that table is the source of
  // truth; do NOT duplicate priority logic here.
  spokespersonRole: string;
  // Originating thread + operator message. Stashed in the Decision payload
  // so the Python `interview_relay.relay_spike_answer()` hook in
  // `scheduled/execute_approved.py` can post the answer back into THIS
  // chat thread when the engineer crew finishes. Without these, the
  // answer is invisible in the Leadership Room.
  threadId: string;
  messageId: string;
}): Promise<{ id: string; reused: boolean }> {
  const s = sql();
  const normalizedQuestion = args.question.trim();
  const autoApprove = autoApproveInvestigationFor(args.spokespersonRole);
  const priority = priorityForLeadershipRole(args.spokespersonRole);
  const existing = (await s`
    SELECT id::text AS id, status
    FROM decisions
    WHERE project = ${args.project}
      AND status IN ('pending', 'approved')
      AND payload->>'spike_source' = 'spokesperson_interview'
      AND payload->>'question' = ${normalizedQuestion}
    ORDER BY created_at DESC
    LIMIT 1
  `) as Array<{ id: string; status: string }>;
  if (existing[0]) {
    const reason = `auto-approved expedited investigation requested by ${prettyRole(args.spokespersonRole)}`;
    if (autoApprove && existing[0].status === "pending") {
      await s`
        UPDATE decisions
        SET
          status = 'approved',
          resolved_at = NOW(),
          payload = jsonb_set(
            jsonb_set(
              jsonb_set(
                jsonb_set(
                  jsonb_set(payload, '{status}', '"approved"'::jsonb),
                  '{resolved_reason}', to_jsonb(${reason}::text)
                ),
                '{requested_by_role}', to_jsonb(${args.spokespersonRole}::text)
              ),
              '{priority}', to_jsonb(${priority}::text)
            ),
            '{expedited}', to_jsonb(true)
          )
        WHERE id = ${existing[0].id}::uuid
      `;
      await s`
        INSERT INTO activity_log (ts, event, project, role, decision_id, crew, run_id, error, payload)
        VALUES (
          NOW(), 'decision_resolved', ${args.project}, ${args.spokespersonRole}, ${existing[0].id},
          'leadership_room', ${`spike-approved-${existing[0].id}`}, NULL,
          ${JSON.stringify({
            agents: [args.spokespersonRole, args.ownerRole],
            status: "approved",
            requested_by_role: args.spokespersonRole,
            priority,
            expedited: true,
            reason,
          })}::jsonb
        )
      `;
    } else if (autoApprove) {
      await s`
        UPDATE decisions
        SET payload = jsonb_set(
          jsonb_set(
            jsonb_set(payload, '{requested_by_role}', to_jsonb(${args.spokespersonRole}::text)),
            '{priority}', to_jsonb(${priority}::text)
          ),
          '{expedited}', to_jsonb(true)
        )
        WHERE id = ${existing[0].id}::uuid
          AND payload->>'spike_source' = 'spokesperson_interview'
          AND payload->>'question' = ${normalizedQuestion}
      `;
    }
    return { id: existing[0].id, reused: true };
  }

  const id = crypto.randomUUID();
  const now = nowIso();
  const summary = `SPIKE: Discover ${args.project} ${humanKind(args.kind)} answer`;
  const status = autoApprove ? "approved" : "pending";
  const resolvedReason = autoApprove
    ? `auto-approved expedited investigation requested by ${prettyRole(args.spokespersonRole)}`
    : null;
  const rationale = (
    `The operator asked: "${normalizedQuestion}". The spokesperson consulted ` +
    `${args.consultedRoles.map(prettyRole).join(", ")} but did not find verified evidence in ` +
    "stored memory or recorded engineer-run file history. Queue a bounded read-only investigation and report the answer back to the spokesperson thread."
  );
  const diffOrPlan = [
    `1. Inspect ${args.project} repository configuration and docs for deployment/runtime evidence.`,
    "2. Identify the UI host, environment, and confidence level without exposing secret values.",
    "3. Record citations: files, PRs, docs, or deployment config inspected.",
    "4. Return a concise answer that the spokesperson can relay to the operator.",
  ].join("\n");
  // The Python model also derives this from requested_by_role, but the web
  // Sprint Board reads raw JSON. Stamp it explicitly so the operator sees P1.
  const payload = {
    id,
    project: args.project,
    type: "other",
    summary,
    rationale,
    diff_or_plan: diffOrPlan,
    risk: "low",
    proposer_role: args.ownerRole,
    proposer_agent_id: `${args.ownerRole}@${args.project}`,
    proposer_display_name: `${prettyRole(args.ownerRole)} SPIKE`,
    status,
    requested_by_role: args.spokespersonRole,
    priority,
    expedited: autoApprove,
    critique: null,
    security_review: null,
    portfolio_review: null,
    paired_decision_id: null,
    pr_url: null,
    base_sha: null,
    created_at: now,
    resolved_at: autoApprove ? now : null,
    resolved_reason: resolvedReason,
    spike_source: "spokesperson_interview",
    question: normalizedQuestion,
    consulted_roles: args.consultedRoles,
    thread_id: args.threadId,
    message_id: args.messageId,
  };
  await s`
    INSERT INTO decisions (id, project, status, type, risk, created_at, resolved_at, payload)
    VALUES (
      ${id}::uuid,
      ${args.project},
      ${status},
      'other',
      'low',
      NOW(),
      ${autoApprove ? now : null}::timestamptz,
      ${JSON.stringify(payload)}::jsonb
    )
  `;
  await s`
    INSERT INTO activity_log (ts, event, project, decision_id, crew, run_id, error, payload)
    VALUES (
      NOW(), 'decision_submitted', ${args.project}, ${id}, 'leadership_room',
      ${`spike-${id}`}, NULL,
      ${JSON.stringify({
        agents: [args.ownerRole],
        spike: true,
        status,
        requested_by_role: args.spokespersonRole,
        priority,
        expedited: autoApprove,
      })}::jsonb
    )
  `;
  if (autoApprove) {
    await s`
      INSERT INTO activity_log (ts, event, project, role, decision_id, crew, run_id, error, payload)
      VALUES (
        NOW(), 'decision_resolved', ${args.project}, ${args.spokespersonRole}, ${id},
        'leadership_room', ${`spike-approved-${id}`}, NULL,
        ${JSON.stringify({
          agents: [args.spokespersonRole, args.ownerRole],
          status: "approved",
          requested_by_role: args.spokespersonRole,
          priority,
          expedited: true,
          reason: resolvedReason,
        })}::jsonb
      )
    `;
  }
  return { id, reused: false };
}

function composeAnswer(args: {
  spokespersonRole: string;
  project: string | null;
  selectedProject: string | null;
  mentionedProject: string | null;
  kind: QuestionKind;
  confidence: "high" | "medium" | "low" | "unknown" | string;
  consultations: Array<{
    consulted_role: string;
    memory_summary: string | null;
    code_scan_summary: string | null;
    files_inspected: string[];
  }>;
  projectCitations: Citation[];
  followUps: string[];
  spikeDecisionId: string | null;
}): string {
  const scope = args.project ?? "the organization";
  const consulted = args.consultations.map((c) => prettyRole(c.consulted_role));
  const files = Array.from(new Set(args.consultations.flatMap((c) => c.files_inspected)));
  const usefulMemory = args.consultations
    .flatMap((c) => (hasUsefulMemory(c.memory_summary) ? [c.memory_summary as string] : []))
    .slice(0, 2);
  const projectSwitch =
    args.mentionedProject &&
    args.selectedProject &&
    args.mentionedProject.toLowerCase() !== args.selectedProject.toLowerCase()
      ? `I treated this as a question about ${args.mentionedProject}, even though ${args.selectedProject} was selected, because the question named ${args.mentionedProject}.`
      : null;

  const paragraphs: string[] = [];
  if (args.kind === "repo_inventory") {
    const repos = MANAGED_REPOS.filter((repo) => repo.status === "active");
    paragraphs.push(
      `The active GitHub repos currently managed by the minions org are:\n\n${repos
        .map((repo) => `- ${repo.project}: ${repo.repo}`)
        .join("\n")}`,
    );
    const deferred = MANAGED_REPOS.filter((repo) => repo.status === "deferred");
    if (deferred.length > 0) {
      paragraphs.push(
        `Deferred or local-only projects I am not counting as active GitHub repos: ${deferred
          .map((repo) => repo.project)
          .join(", ")}.`,
      );
    }
  } else if (args.kind === "functional") {
    paragraphs.push(...composeFunctionalAnswer(args));
  } else if (args.kind === "deployment" && args.confidence === "low") {
    if (args.spikeDecisionId) {
      const priority = priorityForLeadershipRole(args.spokespersonRole).toUpperCase();
      paragraphs.push(
        `I do not have verified deployment evidence for ${scope} yet, so I opened a ${priority} expedited investigation for ${prettyRole(ownerForKind(args.kind))}: ${args.spikeDecisionId.slice(0, 8)}.`,
      );
      paragraphs.push(
        "The expected output is a short deployment answer with the host, environment, confidence level, and file or PR citations. I will not treat this as answered until that evidence comes back.",
      );
    } else {
      paragraphs.push(
        `I do not have verified deployment evidence for ${scope} yet. I checked the stored project records and recent engineer-run file history, but I did not find deployment/config files such as Vercel, Fly, Render, Docker, Railway, or runtime environment configuration.`,
      );
    }
  } else if (args.confidence === "low") {
    paragraphs.push(
      `I do not have enough verified evidence to answer confidently for ${scope} yet.`,
    );
  } else {
    paragraphs.push(
      `For ${scope}, the best grounded answer I can give is based on the records currently in the console.`,
    );
  }

  if (projectSwitch) paragraphs.push(projectSwitch);
  if (args.kind === "repo_inventory" && args.selectedProject) {
    paragraphs.push(
      `I answered at organization scope instead of ${args.selectedProject}, because you asked for repos owned by the minions org.`,
    );
  }

  const evidenceBits: string[] = [];
  if (files.length > 0) {
    evidenceBits.push(`inspected recorded file history: ${files.slice(0, 6).join(", ")}`);
  }
  if (usefulMemory.length > 0) {
    evidenceBits.push(`role memory: ${usefulMemory.join("; ")}`);
  }
  if (args.kind !== "repo_inventory" && args.kind !== "functional") {
    const recentDecisions = args.projectCitations
      .filter((c) => c.source_type === "decision")
      .slice(0, 3)
      .map((c) => c.excerpt);
    if (recentDecisions.length > 0) {
      evidenceBits.push(`recent decisions: ${recentDecisions.join("; ")}`);
    }
  }
  const alreadyOpenedDeploymentSpike =
    args.kind === "deployment" && args.confidence === "low" && args.spikeDecisionId;
  if (evidenceBits.length > 0 && args.kind !== "functional" && !alreadyOpenedDeploymentSpike) {
    paragraphs.push(`What I found: ${evidenceBits.join(". ")}.`);
  }

  if (
    consulted.length > 0 &&
    args.kind !== "repo_inventory" &&
    args.kind !== "functional" &&
    !alreadyOpenedDeploymentSpike
  ) {
    paragraphs.push(`I asked ${consulted.join(", ")} to weigh in on this ${args.kind} question.`);
  } else if (consulted.length > 0 && args.kind === "repo_inventory") {
    paragraphs.push(`For this org-inventory question, I checked the portfolio registry and asked ${consulted.join(", ")} to validate the scope.`);
  }

  if (args.followUps.length > 0) {
    if (args.spikeDecisionId) {
      if (!alreadyOpenedDeploymentSpike) {
        paragraphs.push(
          `I opened a Sprint Board investigation for ${prettyRole(ownerForKind(args.kind))} to verify this and report back: ${args.spikeDecisionId.slice(0, 8)}. Once that investigation completes, leadership can bring the concrete answer back into this room.`,
        );
      }
    } else {
      paragraphs.push(
        `Recommended next step: ${args.followUps[0]}. That should be owned by ${prettyRole(ownerForKind(args.kind))}.`,
      );
    }
  }

  return paragraphs.join("\n\n");
}

function composeFunctionalAnswer(args: {
  spokespersonRole: string;
  project: string | null;
  confidence: string;
  projectCitations: Citation[];
}): string[] {
  const scope = args.project ?? "the organization";
  const proposals = args.projectCitations
    .filter((citation) => citation.source_type === "decision")
    .map((citation) => citation.excerpt)
    .filter((excerpt) => !/\bSPIKE:|deployment answer|dry run|Planning conversation/i.test(excerpt))
    .slice(0, 3);
  const approved = proposals.filter((excerpt) => /\bapproved\b|\bexecuted\b/i.test(excerpt));
  const items = approved.length > 0 ? approved : proposals;
  const owner = prettyRole(args.spokespersonRole);
  const brief = bestSprintBrief(items);

  if (items.length === 0 || brief === null) {
    return [
      `For ${scope}, I do not see a clean sprint-scope record yet.`,
      "What I can say: there are project records in the console, but they do not spell out the work in a way I would treat as a reliable sprint commitment.",
      `${owner} should ask the Manager to publish a short sprint brief: goals, committed items, and what is deliberately out of scope.`,
    ];
  }

  const paragraphs = [
    `For ${scope}, the approved sprint scope is ${brief.title}.`,
    brief.goal
      ? `Sprint goal: ${brief.goal}`
      : "Sprint goal: tighten the current product path and reduce launch risk.",
  ];
  if (brief.committed.length > 0) {
    paragraphs.push([
      "Committed scope:",
      ...brief.committed.slice(0, 6).map((item) => `- ${item}`),
    ].join("\n"));
  }
  if (brief.done.length > 0) {
    paragraphs.push([
      "What I will treat as done:",
      ...brief.done.slice(0, 4).map((item) => `- ${item}`),
    ].join("\n"));
  }
  if (brief.risks.length > 0) {
    paragraphs.push([
      "Risks or watch points:",
      ...brief.risks.slice(0, 3).map((item) => `- ${item}`),
    ].join("\n"));
  }
  paragraphs.push(
    `${owner} view: this is an approved sprint, so I would manage it as the team's current commitment. I would keep investor-facing language focused on the sprint goal and the committed outcomes, not the internal planning transcript.`,
  );
  return paragraphs;
}

function humanizeDecisionExcerpt(excerpt: string): string {
  const title =
    matchClean(excerpt, /Sprint Title\s*\**\s*["“]?([^"\n—-]{6,120})/i) ||
    matchClean(excerpt, /Sprint Proposal:\s*([^*#\n]{6,120})/i) ||
    matchClean(excerpt, /Sprint Goal\s*([^#\n]{12,180})/i);
  if (title) return title;

  const cleaned = excerpt
    .replace(/^[^:]+:\s*/, "")
    .replace(/\bSprint proposal for\b/i, "Sprint work for")
    .replace(/\bapproved:\s*/i, "")
    .replace(/\bexecuted:\s*/i, "In execution: ")
    .replace(/[#*_`>-]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  return compactText(cleaned, 180);
}

type SprintBrief = {
  title: string;
  goal: string | null;
  committed: string[];
  done: string[];
  risks: string[];
};

function bestSprintBrief(excerpts: string[]): SprintBrief | null {
  const parsed = excerpts
    .map(parseSprintBrief)
    .filter((brief): brief is SprintBrief => brief !== null);
  if (parsed.length === 0) return null;
  return parsed.sort((a, b) => scoreSprintBrief(b) - scoreSprintBrief(a))[0];
}

function parseSprintBrief(excerpt: string): SprintBrief | null {
  const plan = excerpt.includes("Plan:") ? excerpt.split("Plan:").slice(1).join("Plan:") : excerpt;
  const title = humanizeDecisionExcerpt(excerpt);
  const goal =
    extractSectionLine(plan, ["Sprint Goal", "Goal", "Objective"]) ||
    extractAfterLabel(plan, /Sprint Goal\s*[:\-]?\s*([^#\n]{20,260})/i);
  let committed = extractSectionItems(plan, [
    "Committed Scope",
    "Scope",
    "Deliverables",
    "Key Deliverables",
    "Sprint Backlog",
    "Planned Work",
    "In Scope",
  ]);
  if (committed.length === 0) committed = fallbackCommittedScope(plan, goal);
  const done = extractSectionItems(plan, [
    "Acceptance Criteria",
    "Definition of Done",
    "Done",
    "Success Criteria",
    "Expected Outcome",
  ]);
  const risks = extractSectionItems(plan, [
    "Risks",
    "Risk",
    "Watch Points",
    "Dependencies",
    "Blockers",
  ]);
  if (!title && !goal && committed.length === 0) return null;
  return {
    title: title || "the approved sprint proposal",
    goal,
    committed,
    done,
    risks,
  };
}

function fallbackCommittedScope(plan: string, goal: string | null): string[] {
  const text = `${goal ?? ""} ${plan}`.toLowerCase();
  const items: string[] = [];
  if (/checkout|payment|order/.test(text)) {
    items.push("Stabilize the checkout, payment, and order-creation path so purchase flow correctness is protected.");
  }
  if (/inventory|stock/.test(text)) {
    items.push("Tighten inventory correctness so orders do not drift from available stock.");
  }
  if (/mobile|conversion/.test(text)) {
    items.push("Unblock the mobile conversion path and remove launch blockers that could hurt buyer completion.");
  }
  if (/financial|money|charge|pricing/.test(text)) {
    items.push("Protect financial integrity around charges, totals, and order state.");
  }
  if (/security|secret|auth|token/.test(text)) {
    items.push("Reduce launch security risk around sensitive configuration and access paths.");
  }
  if (/test|qa|validation|verify/.test(text)) {
    items.push("Add enough validation and QA coverage that the team can trust the launch-critical path.");
  }
  return Array.from(new Set(items)).slice(0, 6);
}

function scoreSprintBrief(brief: SprintBrief): number {
  return brief.committed.length * 3 + brief.done.length * 2 + brief.risks.length + (brief.goal ? 4 : 0);
}

function extractSectionLine(text: string, headings: string[]): string | null {
  for (const heading of headings) {
    const section = extractSection(text, heading);
    if (!section) continue;
    const cleaned = firstUsefulLine(section);
    if (cleaned) return cleaned;
  }
  return null;
}

function extractSectionItems(text: string, headings: string[]): string[] {
  for (const heading of headings) {
    const section = extractSection(text, heading);
    if (!section) continue;
    const items = section
      .split(/\n|(?:^|\s)(?:[-*•]|\d+[.)])\s+/)
      .map(cleanSprintLine)
      .filter((line) => line.length > 8)
      .filter((line) => !/^sprint|^status|^project|^prepared by/i.test(line))
      .slice(0, 8);
    if (items.length > 0) return Array.from(new Set(items));
  }
  return [];
}

function extractSection(text: string, heading: string): string | null {
  const pattern = new RegExp(
    `(?:^|\\n)\\s*#{0,4}\\s*(?:\\*\\*)?${escapeRegExp(heading)}(?:\\*\\*)?\\s*[:\\-]?\\s*\\n?([\\s\\S]*?)(?=\\n\\s*#{1,4}\\s|\\n\\s*(?:\\*\\*)?[A-Z][A-Za-z /+&-]{2,60}(?:\\*\\*)?\\s*[:\\-]\\s*\\n|$)`,
    "i",
  );
  return pattern.exec(text)?.[1] ?? null;
}

function firstUsefulLine(section: string): string | null {
  return section
    .split(/\n/)
    .map(cleanSprintLine)
    .find((line) => line.length > 12 && !/^[-*•]?$/.test(line)) ?? null;
}

function extractAfterLabel(text: string, pattern: RegExp): string | null {
  const match = pattern.exec(text);
  return match?.[1] ? cleanSprintLine(match[1]) : null;
}

function cleanSprintLine(line: string): string {
  return compactText(
    line
      .replace(/^[\s\-*•\d.)]+/, "")
      .replace(/[#*_`>"“”]/g, " ")
      .replace(/\s+/g, " ")
      .trim()
      .replace(/[.:;,—-]+$/, ""),
    220,
  );
}

function matchClean(text: string, pattern: RegExp): string | null {
  const match = pattern.exec(text);
  if (!match?.[1]) return null;
  return match[1]
    .replace(/[#*_`>"“”]/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/[.:;,—-]+$/, "");
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function prettyRole(role: string): string {
  return role.replaceAll("_", " ").replace(/\b\w/g, (char) => char.toUpperCase());
}

function humanKind(kind: QuestionKind): string {
  return kind === "deployment" ? "deployment" : kind.replaceAll("_", " ");
}
