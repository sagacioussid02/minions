/**
 * Tenant resolution for the web side (P3 of public-saas-onboarding).
 *
 * `getTenantId()` is the ONLY place the web resolves a Clerk session into an
 * internal tenant id. It creates the tenant row on first sight (idempotent),
 * mirroring the Python `minions.tenants` store + provisioning logic so both
 * sides agree on founder detection and trial defaults.
 *
 * The founder is identified by `MINIONS_FOUNDER_CLERK_ID` and pinned to the
 * fixed `MINIONS_FOUNDER_TENANT_ID` — env only, never hardcoded, so the
 * founder stays swappable and no identity lives in the public repo.
 */

import { auth } from "@clerk/nextjs/server";
import { sql } from "./db";

const FREE_TRIAL_DAYS = 14; // one sprint
const FOUNDER_PROJECT_CAP = 9999; // founder is cap-exempt
const FOUNDER_COST_CAP_USD = 1_000_000;

export type Tenant = {
  tenant_id: string;
  clerk_user_id: string;
  plan: "free" | "starter" | "pro";
  founder: boolean;
  cost_cap_daily_usd: number;
  project_cap: number;
  trial_expires_at: string | null;
  trial_extensions_used: number;
};

function founderClerkId(): string | null {
  return process.env.MINIONS_FOUNDER_CLERK_ID || null;
}

function founderTenantId(): string | null {
  return process.env.MINIONS_FOUNDER_TENANT_ID || null;
}

export async function getTenantByClerkId(
  clerkUserId: string,
): Promise<Tenant | null> {
  const db = sql();
  const rows = (await db`
    SELECT tenant_id, clerk_user_id, plan, founder, cost_cap_daily_usd,
           project_cap, trial_expires_at, trial_extensions_used
    FROM tenants WHERE clerk_user_id = ${clerkUserId} LIMIT 1
  `) as Tenant[];
  return rows[0] ?? null;
}

async function createTenant(clerkUserId: string): Promise<Tenant> {
  const db = sql();
  const isFounder =
    founderClerkId() !== null && clerkUserId === founderClerkId();

  if (isFounder) {
    const tid = founderTenantId() ?? crypto.randomUUID();
    const rows = (await db`
      INSERT INTO tenants
        (tenant_id, clerk_user_id, plan, founder, cost_cap_daily_usd, project_cap, trial_expires_at)
      VALUES
        (${tid}, ${clerkUserId}, 'pro', true, ${FOUNDER_COST_CAP_USD}, ${FOUNDER_PROJECT_CAP}, NULL)
      ON CONFLICT (clerk_user_id) DO NOTHING
      RETURNING tenant_id, clerk_user_id, plan, founder, cost_cap_daily_usd,
                project_cap, trial_expires_at, trial_extensions_used
    `) as Tenant[];
    return rows[0] ?? ((await getTenantByClerkId(clerkUserId)) as Tenant);
  }

  const rows = (await db`
    INSERT INTO tenants
      (tenant_id, clerk_user_id, plan, founder, cost_cap_daily_usd, project_cap, trial_expires_at)
    VALUES
      (gen_random_uuid(), ${clerkUserId}, 'free', false, 1.0, 2,
       NOW() + make_interval(days => ${FREE_TRIAL_DAYS}))
    ON CONFLICT (clerk_user_id) DO NOTHING
    RETURNING tenant_id, clerk_user_id, plan, founder, cost_cap_daily_usd,
              project_cap, trial_expires_at, trial_extensions_used
  `) as Tenant[];
  return rows[0] ?? ((await getTenantByClerkId(clerkUserId)) as Tenant);
}

/** The current tenant, creating it on first sight. Requires a Clerk session. */
export async function getCurrentTenant(): Promise<Tenant> {
  const { userId } = await auth();
  if (!userId) {
    throw new Error("getCurrentTenant called without an authenticated session");
  }
  return (await getTenantByClerkId(userId)) ?? (await createTenant(userId));
}

/**
 * The only way an API route or server component obtains the current tenant id.
 */
export async function getTenantId(): Promise<string> {
  return (await getCurrentTenant()).tenant_id;
}
