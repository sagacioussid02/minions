import { NextRequest, NextResponse } from "next/server";
import { getCurrentTenant } from "@/lib/tenant";
import { saveOnboardingStep } from "@/lib/onboarding";
import type { PickedRepo } from "@/lib/tenant-projects";

/** Step A submit: save the chosen repos, advance to the manifest step. */
export async function POST(req: NextRequest) {
  const tenant = await getCurrentTenant();
  const body = (await req.json().catch(() => null)) as { repos?: PickedRepo[] } | null;
  const repos = body?.repos ?? [];

  if (repos.length === 0) {
    return NextResponse.json({ error: "pick at least one repo" }, { status: 400 });
  }
  if (repos.length > tenant.project_cap) {
    return NextResponse.json(
      { error: "project_cap_reached", cap: tenant.project_cap },
      { status: 409 },
    );
  }
  await saveOnboardingStep(tenant.tenant_id, "manifest", { repos });
  return NextResponse.json({ ok: true });
}
