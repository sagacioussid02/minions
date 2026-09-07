/**
 * tenant_github_installations read/write (P7). Stores the GitHub App
 * installation id per tenant — never a token (tokens are minted on demand,
 * see lib/github-app.ts).
 */

import { sql } from "./db";

export type TenantInstallation = {
  installation_id: number;
  account_login: string;
};

export async function saveInstallation(
  tenantId: string,
  installationId: number,
  accountLogin: string,
): Promise<void> {
  const db = sql();
  await db`
    INSERT INTO tenant_github_installations (tenant_id, installation_id, account_login)
    VALUES (${tenantId}, ${installationId}, ${accountLogin})
    ON CONFLICT (tenant_id, installation_id) DO UPDATE SET account_login = EXCLUDED.account_login
  `;
}

/** Most recent installation for a tenant, or null. */
export async function getInstallation(
  tenantId: string,
): Promise<TenantInstallation | null> {
  const db = sql();
  const rows = (await db`
    SELECT installation_id, account_login
    FROM tenant_github_installations
    WHERE tenant_id = ${tenantId}
    ORDER BY created_at DESC
    LIMIT 1
  `) as TenantInstallation[];
  return rows[0] ?? null;
}
