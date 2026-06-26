/**
 * Onboarding wizard state (P6 of public-saas-onboarding).
 *
 * Backed by the `tenant_onboarding` table. The wizard is checkpointable:
 * `step` records the furthest screen reached, `payload` accumulates answers.
 * A tenant with no row is treated as "repos" (start of the wizard); the
 * founder is seeded `complete` so they never enter it.
 */

import { sql } from "./db";

export const ONBOARDING_STEPS = ["repos", "manifest", "dossier"] as const;
export type OnboardingStep = (typeof ONBOARDING_STEPS)[number] | "complete";

type OnboardingRow = {
  step: OnboardingStep;
  payload: Record<string, unknown>;
};

/** Current step for a tenant. No row → "repos" (wizard not started). */
export async function getOnboarding(tenantId: string): Promise<OnboardingRow> {
  const db = sql();
  const rows = (await db`
    SELECT step, payload FROM tenant_onboarding WHERE tenant_id = ${tenantId} LIMIT 1
  `) as OnboardingRow[];
  return rows[0] ?? { step: "repos", payload: {} };
}

export async function isOnboardingComplete(tenantId: string): Promise<boolean> {
  return (await getOnboarding(tenantId)).step === "complete";
}

/**
 * Upsert the tenant's step + merge payload. Payload is shallow-merged so each
 * step can save its slice without clobbering earlier answers.
 */
export async function saveOnboardingStep(
  tenantId: string,
  step: OnboardingStep,
  payload: Record<string, unknown> = {},
): Promise<void> {
  const db = sql();
  await db`
    INSERT INTO tenant_onboarding (tenant_id, step, payload, updated_at)
    VALUES (${tenantId}, ${step}, ${JSON.stringify(payload)}::jsonb, NOW())
    ON CONFLICT (tenant_id) DO UPDATE SET
      step = EXCLUDED.step,
      payload = tenant_onboarding.payload || EXCLUDED.payload,
      updated_at = NOW()
  `;
}
