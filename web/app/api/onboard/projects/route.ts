import { NextRequest, NextResponse } from "next/server";
import { currentUser } from "@clerk/nextjs/server";
import { getCurrentTenant } from "@/lib/tenant";
import { saveOnboardingStep } from "@/lib/onboarding";
import { buildManifest, countProjects, createProject } from "@/lib/tenant-projects";

type ProjectForm = {
  name: string;
  description: string;
  repoFullName: string;
  defaultBranch: string;
  weeklyBudgetUsd: number;
  monthlyBudgetUsd: number;
};

const slug = (s: string) =>
  s.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");

/** Step B submit: create tenant_projects (cap-enforced), advance to dossier. */
export async function POST(req: NextRequest) {
  const tenant = await getCurrentTenant();
  const body = (await req.json().catch(() => null)) as { projects?: ProjectForm[] } | null;
  const projects = body?.projects ?? [];

  if (projects.length === 0) {
    return NextResponse.json({ error: "no projects" }, { status: 400 });
  }
  for (const p of projects) {
    if (!p.name?.trim() || !p.repoFullName?.trim()) {
      return NextResponse.json({ error: "name and repo are required" }, { status: 400 });
    }
  }

  // Defense-in-depth cap check (the wizard already caps repo selection).
  const existing = await countProjects(tenant.tenant_id);
  if (existing + projects.length > tenant.project_cap) {
    return NextResponse.json(
      { error: "project_cap_reached", cap: tenant.project_cap },
      { status: 409 },
    );
  }

  // Manifest.owner is required Python-side (used as a notify fallback if the
  // Clerk email lookup fails at run time) — resolve it once here rather than
  // leaving it unset and failing Manifest validation for every tenant project.
  const user = await currentUser();
  const owner = user?.primaryEmailAddress?.emailAddress ?? user?.emailAddresses[0]?.emailAddress ?? tenant.clerk_user_id;

  for (const p of projects) {
    const project = slug(p.name);
    await createProject(
      tenant.tenant_id,
      project,
      buildManifest({
        name: p.name,
        description: p.description ?? "",
        repoFullName: p.repoFullName,
        defaultBranch: p.defaultBranch || "main",
        weeklyBudgetUsd: Number(p.weeklyBudgetUsd) || 25,
        monthlyBudgetUsd: Number(p.monthlyBudgetUsd) || 100,
        owner,
      }),
    );
  }

  await saveOnboardingStep(tenant.tenant_id, "dossier", {});
  return NextResponse.json({ ok: true });
}
