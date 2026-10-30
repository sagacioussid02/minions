import { NextRequest, NextResponse } from "next/server";
import { getCurrentTenant } from "@/lib/tenant";
import { sql } from "@/lib/db";
import { dispatchTenantWorkflow } from "@/lib/actions-dispatch";

export const dynamic = "force-dynamic";

// Keep in step with MAX_REQUEST_CHARS in minions/models/work_request.py and the
// CHECK constraint in migration 0022_work_requests.sql.
const MAX_TEXT = 4000;
const MAX_QUEUED_PER_TENANT = 20;

/**
 * Operator work requests: free text ("add a famous persona to my personas project")
 * that the crew's PM scopes into issues. The web app cannot run the crew, so this
 * queues a row and dispatches the `requests.yml` workflow; `minions cron requests`
 * drains it. The text is stored verbatim and treated as untrusted downstream.
 */
export async function POST(req: NextRequest) {
  const tenant = await getCurrentTenant();
  const body = (await req.json().catch(() => null)) as {
    project?: string;
    text?: string;
  } | null;
  if (typeof body?.text !== "string" || typeof body?.project !== "string") {
    return NextResponse.json({ error: "text and project must be strings" }, { status: 400 });
  }
  const text = body.text.trim();
  const slug = body.project.trim();

  if (!text) return NextResponse.json({ error: "text required" }, { status: 400 });
  if (text.length > MAX_TEXT) {
    return NextResponse.json({ error: `text over ${MAX_TEXT} chars` }, { status: 400 });
  }
  if (!/^[\w.-]{1,64}$/.test(slug)) {
    return NextResponse.json({ error: "invalid project" }, { status: 400 });
  }

  const db = sql();

  // Resolve the project from the DB, never from the client: a non-founder tenant
  // may only ask for work on projects it owns. The founder's projects live in
  // projects/*.yaml, so the engine checks those when it drains the queue.
  let project = slug;
  if (!tenant.founder) {
    const owned = (await db`
      SELECT manifest_json->>'name' AS name
      FROM tenant_projects WHERE tenant_id = ${tenant.tenant_id} AND project = ${slug}
    `) as { name: string | null }[];
    if (owned.length === 0) {
      return NextResponse.json({ error: "unknown project" }, { status: 404 });
    }
    project = owned[0].name ?? slug;
  }

  const queued = (await db`
    SELECT count(*)::int AS n FROM work_requests
    WHERE tenant_id = ${tenant.tenant_id} AND status IN ('queued', 'clarifying')
  `) as { n: number }[];
  if ((queued[0]?.n ?? 0) >= MAX_QUEUED_PER_TENANT) {
    return NextResponse.json({ error: "too many open requests" }, { status: 429 });
  }

  const id = crypto.randomUUID();
  await db`
    INSERT INTO work_requests (id, project, text, source, status, tenant_id)
    VALUES (${id}, ${project}, ${text}, 'console', 'queued', ${tenant.tenant_id})
  `;

  // Best-effort: the 6-hourly schedule drains the queue if the dispatch fails.
  await dispatchTenantWorkflow("requests.yml", {});

  return NextResponse.json({ ok: true, id }, { status: 202 });
}

/** Status of the signed-in tenant's requests (newest first), or one by ?id=. */
export async function GET(req: NextRequest) {
  const tenant = await getCurrentTenant();
  const id = req.nextUrl.searchParams.get("id");
  if (id && !/^[0-9a-f-]{36}$/i.test(id)) {
    return NextResponse.json({ error: "invalid id" }, { status: 400 });
  }
  const db = sql();
  const rows = id
    ? await db`
        SELECT id, project, text, status, question_ids, result, created_at
        FROM work_requests WHERE tenant_id = ${tenant.tenant_id} AND id = ${id}
      `
    : await db`
        SELECT id, project, text, status, question_ids, result, created_at
        FROM work_requests WHERE tenant_id = ${tenant.tenant_id}
        ORDER BY created_at DESC LIMIT 50
      `;
  return NextResponse.json({ requests: rows });
}
