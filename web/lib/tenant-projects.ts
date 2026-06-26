/**
 * tenant_projects read/write (P7). The per-tenant equivalent of
 * projects/*.yaml; manifest_json is shaped like the Python `Manifest` model
 * so the cron's load_tenant_manifests() can Manifest.model_validate it at
 * go-live.
 */

import { sql } from "./db";

export async function countProjects(tenantId: string): Promise<number> {
  const db = sql();
  const rows = (await db`
    SELECT count(*)::int AS n FROM tenant_projects WHERE tenant_id = ${tenantId}
  `) as { n: number }[];
  return rows[0]?.n ?? 0;
}

export async function createProject(
  tenantId: string,
  project: string,
  manifest: Record<string, unknown>,
): Promise<void> {
  const db = sql();
  await db`
    INSERT INTO tenant_projects (tenant_id, project, manifest_json)
    VALUES (${tenantId}, ${project}, ${JSON.stringify(manifest)}::jsonb)
    ON CONFLICT (tenant_id, project) DO UPDATE SET manifest_json = EXCLUDED.manifest_json
  `;
}

/** A repo the user picked in Step A, carried in onboarding payload. */
export type PickedRepo = { full_name: string; default_branch: string };

/** Build a Manifest-shaped dict from the Step B form for one project. */
export function buildManifest(input: {
  name: string;
  description: string;
  repoFullName: string;
  defaultBranch: string;
  weeklyBudgetUsd: number;
  monthlyBudgetUsd: number;
}): Record<string, unknown> {
  return {
    name: input.name,
    description: input.description,
    source: {
      kind: "github",
      repo: input.repoFullName,
      default_branch: input.defaultBranch,
    },
    weekly_budget_usd: input.weeklyBudgetUsd,
    monthly_budget_usd: input.monthlyBudgetUsd,
  };
}
