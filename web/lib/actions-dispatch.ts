/**
 * Fire a GitHub Actions workflow_dispatch for a tenant-scoped cron
 * (tenant_bootstrap.yml, tenant_execute_approved.yml, ...). Best-effort:
 * never throws — a dispatch failure shouldn't block the web request that
 * triggered it (onboarding completion, decision approval). Requires
 * MINIONS_ACTIONS_DISPATCH_TOKEN (a GitHub PAT scoped to actions:write on
 * this repo only); silently no-ops if unset.
 */

const DISPATCH_REPO = "sagacioussid02/minions";

export async function dispatchTenantWorkflow(
  workflow: string,
  inputs: Record<string, string>,
): Promise<void> {
  const token = process.env.MINIONS_ACTIONS_DISPATCH_TOKEN;
  if (!token) return;
  try {
    const res = await fetch(
      `https://api.github.com/repos/${DISPATCH_REPO}/actions/workflows/${workflow}/dispatches`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          Accept: "application/vnd.github+json",
          "X-GitHub-Api-Version": "2022-11-28",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ ref: "main", inputs }),
      },
    );
    if (!res.ok) {
      console.error(`[actions-dispatch] ${workflow} -> ${res.status}: ${await res.text()}`);
    }
  } catch (err) {
    // best-effort — see comment above, but still worth logging for diagnosis
    console.error(`[actions-dispatch] ${workflow} threw:`, err);
  }
}
