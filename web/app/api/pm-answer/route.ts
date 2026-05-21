import { NextRequest, NextResponse } from "next/server";
import { sql } from "@/lib/db";
import { PMAnswerSchema } from "@/lib/schemas";

export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
  try {
    const body = (await req.json()) as { project?: string; question?: string };
    const project = (body.project ?? "").trim();
    const question = (body.question ?? "").trim();
    if (!project || !question) {
      return NextResponse.json(
        { error: "project and question are required" },
        { status: 400 },
      );
    }
    const answer = await createPmAnswer(project, question);
    return NextResponse.json(PMAnswerSchema.parse(answer));
  } catch (err) {
    console.error("[/api/pm-answer]", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "internal" },
      { status: 500 },
    );
  }
}

async function createPmAnswer(project: string, question: string) {
  const s = sql();
  await s`
    CREATE TABLE IF NOT EXISTS pm_answers (
      id uuid PRIMARY KEY,
      project text NOT NULL,
      created_at timestamptz NOT NULL,
      payload jsonb NOT NULL
    )
  `;

  const decisionRows = (await s`
    SELECT payload->>'summary' AS summary, id::text AS id
    FROM decisions
    WHERE project = ${project}
      AND COALESCE(payload->>'summary', '') NOT LIKE '%[DRY RUN]%'
    ORDER BY created_at DESC
    LIMIT 5
  `) as Array<{ summary: string | null; id: string }>;

  const [ritualsTable] = (await s`
    SELECT to_regclass('public.agile_rituals') IS NOT NULL AS exists
  `) as Array<{ exists: boolean }>;
  const ritualRows = ritualsTable?.exists
    ? ((await s`
        SELECT payload
        FROM agile_rituals
        WHERE project = ${project}
        ORDER BY created_at DESC
        LIMIT 3
      `) as Array<{ payload: Record<string, unknown> }>)
    : [];

  const q = question.toLowerCase();
  const secretish = /secret|password|token|api key|api_key|rotation/.test(q);
  const id = crypto.randomUUID();
  const citations = [
    ...decisionRows.slice(0, 3).map((d) => `decision:${d.id.slice(0, 8)}`),
    ...ritualRows.slice(0, 2).map((r) => `ritual:${String(r.payload.id ?? "").slice(0, 8)}`),
  ];
  const summaries = decisionRows.map((d) => d.summary).filter(Boolean).join("; ");
  const latestRitual = ritualRows[0]?.payload?.summary;
  const answer = secretish
    ? (
        `For ${project}, I can discuss secret names and rotation process, but not values. ` +
        "Rotation should go through Security Champion or DevSecOps: create the new value, " +
        "update the runtime/repo secret store, deploy, verify, then revoke the old value. " +
        `Recent context: ${String(latestRitual ?? summaries ?? "no recent records")}.`
      )
    : (
        `${project} current PM view: ${String(latestRitual ?? "no Agile ritual yet")}. ` +
        `Recent Decisions: ${summaries || "none recorded"}.`
      );

  const payload = {
    id,
    project,
    question,
    answer,
    citations,
    escalated_to: secretish ? "security_champion" : null,
    created_at: new Date().toISOString(),
  };

  await s`
    INSERT INTO pm_answers (id, project, created_at, payload)
    VALUES (${id}::uuid, ${project}, NOW(), ${JSON.stringify(payload)}::jsonb)
  `;
  await s`
    INSERT INTO activity_log (ts, event, project, decision_id, crew, run_id, error, payload)
    VALUES (
      NOW(), 'pm_answered', ${project}, ${id}, 'pm_spokesperson',
      ${`pm-${project}-${id}`}, NULL, ${JSON.stringify({ agents: ["product_manager"] })}::jsonb
    )
  `;
  return payload;
}
