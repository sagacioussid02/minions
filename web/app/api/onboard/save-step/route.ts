import { NextRequest, NextResponse } from "next/server";
import { getTenantId } from "@/lib/tenant";
import {
  ONBOARDING_STEPS,
  saveOnboardingStep,
  type OnboardingStep,
} from "@/lib/onboarding";

const VALID_STEPS: OnboardingStep[] = [...ONBOARDING_STEPS, "complete"];

export async function POST(req: NextRequest) {
  const tenantId = await getTenantId();
  const body = (await req.json().catch(() => null)) as {
    step?: string;
    payload?: Record<string, unknown>;
  } | null;

  const step = body?.step as OnboardingStep | undefined;
  if (!step || !VALID_STEPS.includes(step)) {
    return NextResponse.json({ error: "invalid step" }, { status: 400 });
  }

  await saveOnboardingStep(tenantId, step, body?.payload ?? {});
  return NextResponse.json({ ok: true, step });
}
