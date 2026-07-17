import { NextRequest, NextResponse } from "next/server";
import { getCurrentTenant } from "@/lib/tenant";
import { saveOnboardingStep } from "@/lib/onboarding";
import { sql } from "@/lib/db";

type DossierAnswers = {
  project: string;
  purpose: string;
  primaryUsers: string;
  topFeatures: string;
  nonGoals: string;
};

const DISPATCH_REPO = "sagacioussid02/minions";

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
  for (const a of answers) {
    const markdown = composeMarkdown(a);
    const payload = {
      id: crypto.randomUUID(),
      project: a.project,
      commit_sha: "onboarding",
      generated_at: new Date().toISOString(),
      status: "drafted",
      markdown,
      sections_present: [],
      crew_version: "onboarding-qa/v1",
    };
    await db`
      INSERT INTO dossier_drafts (id, project, commit_sha, status, generated_at, payload, tenant_id)
      VALUES (${payload.id}, ${a.project}, ${payload.commit_sha}, ${payload.status},
              ${payload.generated_at}, ${JSON.stringify(payload)}::jsonb, ${tenant.tenant_id})
    `;
  }

  await saveOnboardingStep(tenant.tenant_id, "complete", {});

  // Kick off the tenant's first real sprint now instead of waiting for the
  // Monday cron. Best-effort: a dispatch failure shouldn't block onboarding
  // completion — the founder can always trigger it manually.
  const dispatchToken = process.env.MINIONS_ACTIONS_DISPATCH_TOKEN;
  if (dispatchToken) {
    try {
      await fetch(
        `https://api.github.com/repos/${DISPATCH_REPO}/actions/workflows/tenant_bootstrap.yml/dispatches`,
        {
          method: "POST",
          headers: {
            Authorization: `Bearer ${dispatchToken}`,
            Accept: "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
          },
          body: JSON.stringify({ ref: "main", inputs: { tenant_id: tenant.tenant_id } }),
        },
      );
    } catch {
      // best-effort — see comment above
    }
  }

  return NextResponse.json({ ok: true });
}
