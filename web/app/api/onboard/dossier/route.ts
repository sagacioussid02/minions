import { NextRequest, NextResponse } from "next/server";
import { getCurrentTenant } from "@/lib/tenant";
import { saveOnboardingStep } from "@/lib/onboarding";
import { sql } from "@/lib/db";
import { dispatchTenantWorkflow } from "@/lib/actions-dispatch";

type DossierAnswers = {
  project: string;
  purpose: string;
  primaryUsers: string;
  topFeatures: string;
  nonGoals: string;
};

function composeMarkdown(a: DossierAnswers): string {
  return [
    "# Project dossier (onboarding Q&A)",
    "",
    "## Purpose",
    a.purpose || "(not provided)",
    "",
    "## Primary users",
    a.primaryUsers || "(not provided)",
    "",
    "## Top features this month",
    a.topFeatures || "(not provided)",
    "",
    "## Non-goals",
    a.nonGoals || "(not provided)",
  ].join("\n");
}

/**
 * Step C submit (P8): writes a dossier_drafts row per project from the
 * operator's Q&A answers, marks onboarding complete, and dispatches the
 * tenant_bootstrap GitHub Action so the crew starts planning immediately
 * instead of waiting for Monday's cron.
 */
export async function POST(req: NextRequest) {
  const tenant = await getCurrentTenant();
  const body = (await req.json().catch(() => null)) as { answers?: DossierAnswers[] } | null;
  const answers = body?.answers ?? [];

  if (answers.length === 0) {
    return NextResponse.json({ error: "no projects" }, { status: 400 });
  }

  const db = sql();

  // Authoritative slug -> display-name map for this tenant, from the DB —
  // never trust the client's project id. dossier_drafts.project must be the
  // manifest's display *name* (what build_profile()/dossier_store.latest_merged
  // look up by), not the URL slug the client sends; they can differ.
  const owned = (await db`
    SELECT project, manifest_json->>'name' AS name
    FROM tenant_projects WHERE tenant_id = ${tenant.tenant_id}
  `) as { project: string; name: string | null }[];
  const nameBySlug = new Map(owned.map((r) => [r.project, r.name ?? r.project]));

  for (const a of answers) {
    const name = nameBySlug.get(a.project);
    if (!name) continue; // not this tenant's project — skip silently

    const markdown = composeMarkdown(a);
    const payload = {
      id: crypto.randomUUID(),
      project: name,
      commit_sha: "onboarding",
      generated_at: new Date().toISOString(),
      status: "drafted",
      markdown,
      sections_present: [],
      crew_version: "onboarding-qa/v1",
    };
    await db`
      INSERT INTO dossier_drafts (id, project, commit_sha, status, generated_at, payload, tenant_id)
      VALUES (${payload.id}, ${name}, ${payload.commit_sha}, ${payload.status},
              ${payload.generated_at}, ${JSON.stringify(payload)}::jsonb, ${tenant.tenant_id})
    `;
  }

  await saveOnboardingStep(tenant.tenant_id, "complete", {});

  // Kick off the tenant's first real sprint now instead of waiting for the
  // Monday cron. Best-effort: a dispatch failure shouldn't block onboarding
  // completion — the founder can always trigger it manually.
  await dispatchTenantWorkflow("tenant_bootstrap.yml", { tenant_id: tenant.tenant_id });

  return NextResponse.json({ ok: true });
}
