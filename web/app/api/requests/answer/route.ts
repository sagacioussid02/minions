import { NextRequest, NextResponse } from "next/server";
import { getCurrentTenant } from "@/lib/tenant";
import { sql } from "@/lib/db";
import { dispatchTenantWorkflow } from "@/lib/actions-dispatch";

export const dynamic = "force-dynamic";

const UUID = /^[0-9a-f-]{36}$/i;
const MAX_ANSWER = 2000;

/**
 * Answer one of the crew's clarifying questions for a work request. The question must
 * be one the request itself asked (listed in its question_ids) and belong to the
 * signed-in tenant. When the last open question is answered, the requests workflow is
 * dispatched so the crew resumes without waiting for the next scheduled sweep.
 */
export async function POST(req: NextRequest) {
  const tenant = await getCurrentTenant();
  const body = (await req.json().catch(() => null)) as {
    request_id?: string;
    question_id?: string;
    answer?: string;
  } | null;
  const requestId = body?.request_id;
  const questionId = body?.question_id;
  const answer = typeof body?.answer === "string" ? body.answer.trim() : "";
  if (!requestId || !UUID.test(requestId) || !questionId || !UUID.test(questionId)) {
    return NextResponse.json({ error: "invalid ids" }, { status: 400 });
  }
  if (!answer) return NextResponse.json({ error: "answer required" }, { status: 400 });
  if (answer.length > MAX_ANSWER) {
    return NextResponse.json({ error: `answer over ${MAX_ANSWER} chars` }, { status: 400 });
  }

  const db = sql();
  const owned = (await db`
    SELECT question_ids FROM work_requests
    WHERE tenant_id = ${tenant.tenant_id} AND id = ${requestId} AND status = 'clarifying'
  `) as { question_ids: string[] }[];
  if (owned.length === 0 || !(owned[0].question_ids ?? []).includes(questionId)) {
    return NextResponse.json({ error: "unknown question" }, { status: 404 });
  }

  const updated = (await db`
    UPDATE questions
    SET status = 'answered',
        payload = payload || jsonb_build_object(
          'status', 'answered', 'answer', ${answer}::text, 'answered_by', 'operator'::text,
          'answered_at', to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
        )
    WHERE id = ${questionId} AND tenant_id = ${tenant.tenant_id}
      AND status IN ('open', 'escalated')
    RETURNING id
  `) as { id: string }[];
  if (updated.length === 0) {
    return NextResponse.json({ error: "already answered" }, { status: 409 });
  }

  const ids = owned[0].question_ids;
  const left = (await db`
    SELECT count(*)::int AS n FROM questions
    WHERE tenant_id = ${tenant.tenant_id} AND id = ANY(${ids}) AND status <> 'answered'
  `) as { n: number }[];
  const done = (left[0]?.n ?? 1) === 0;
  // Best-effort: the 6-hourly schedule resumes the request if the dispatch fails.
  if (done) await dispatchTenantWorkflow("requests.yml", {});

  return NextResponse.json({ ok: true, all_answered: done });
}
