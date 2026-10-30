import { NextRequest, NextResponse } from "next/server";
import { getCurrentTenant } from "@/lib/tenant";
import { sql } from "@/lib/db";
import { dispatchTenantWorkflow } from "@/lib/actions-dispatch";

export const dynamic = "force-dynamic";

const UUID = /^[0-9a-f-]{36}$/i;
const MAX_ANSWER = 2000;

/**
 * Answer any open question addressed to the operator. If it belongs to a work request
 * (context `request:<id>`) and that was the last open one, the requests workflow is
 * dispatched so the crew resumes without waiting for the next scheduled sweep.
 */
export async function POST(req: NextRequest) {
  const tenant = await getCurrentTenant();
  const body = (await req.json().catch(() => null)) as {
    question_id?: string;
    answer?: string;
  } | null;
  const questionId = body?.question_id;
  const answer = typeof body?.answer === "string" ? body.answer.trim() : "";
  if (!questionId || !UUID.test(questionId)) {
    return NextResponse.json({ error: "invalid question id" }, { status: 400 });
  }
  if (!answer) return NextResponse.json({ error: "answer required" }, { status: 400 });
  if (answer.length > MAX_ANSWER) {
    return NextResponse.json({ error: `answer over ${MAX_ANSWER} chars` }, { status: 400 });
  }

  const db = sql();
  const updated = (await db`
    UPDATE questions
    SET status = 'answered',
        payload = payload || jsonb_build_object(
          'status', 'answered', 'answer', ${answer}::text, 'answered_by', 'operator'::text,
          'answered_at', to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
        )
    WHERE id = ${questionId} AND tenant_id = ${tenant.tenant_id}
      AND status IN ('open', 'escalated')
    RETURNING payload->>'context' AS context
  `) as { context: string | null }[];
  if (updated.length === 0) {
    return NextResponse.json({ error: "not found or already answered" }, { status: 409 });
  }

  let resumed = false;
  const m = /^request:([0-9a-f-]{36})$/i.exec(updated[0].context ?? "");
  if (m) {
    const reqRows = (await db`
      SELECT question_ids FROM work_requests
      WHERE tenant_id = ${tenant.tenant_id} AND id = ${m[1]} AND status = 'clarifying'
    `) as { question_ids: string[] }[];
    if (reqRows.length > 0) {
      const left = (await db`
        SELECT count(*)::int AS n FROM questions
        WHERE tenant_id = ${tenant.tenant_id} AND id = ANY(${reqRows[0].question_ids})
          AND status <> 'answered'
      `) as { n: number }[];
      if ((left[0]?.n ?? 1) === 0) {
        // Best-effort: the 6-hourly schedule resumes the request if this fails.
        await dispatchTenantWorkflow("requests.yml", {});
        resumed = true;
      }
    }
  }
  return NextResponse.json({ ok: true, resumed });
}
