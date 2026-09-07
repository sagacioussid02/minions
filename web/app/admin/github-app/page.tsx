import { redirect } from "next/navigation";
import { headers } from "next/headers";
import { getCurrentTenant } from "@/lib/tenant";
import { getGithubAppConfig } from "@/lib/github-app-config";

export const dynamic = "force-dynamic";
export const metadata = { title: "GitHub App setup" };

async function origin(): Promise<string> {
  const h = await headers();
  const host = h.get("host");
  const proto = h.get("x-forwarded-proto") ?? "https";
  return `${proto}://${host}`;
}

function buildManifest(base: string) {
  return {
    name: "minions",
    url: base,
    hook_attributes: { url: `${base}/api/github-webhook` },
    redirect_url: `${base}/admin/github-app/callback`,
    // Distinct from redirect_url: this fires on every *installation* (not
    // just App creation), sending installers back to /onboard?installation_id=.
    setup_url: `${base}/onboard`,
    setup_on_update: true,
    public: true, // customers install this on their own orgs, not just yours
    default_permissions: {
      contents: "write",
      issues: "write",
      pull_requests: "write",
      metadata: "read",
    },
    // installation / installation_repositories are delivered to every App's
    // webhook automatically — GitHub rejects them if declared explicitly.
  };
}

export default async function GithubAppAdminPage({
  searchParams,
}: {
  searchParams: Promise<{ success?: string }>;
}) {
  const tenant = await getCurrentTenant();
  if (!tenant.founder) redirect("/hq");

  const sp = await searchParams;
  const base = await origin();
  const config = await getGithubAppConfig();
  const manifest = JSON.stringify(buildManifest(base));

  return (
    <div className="flex min-h-screen items-center justify-center bg-[var(--bg-canvas)] p-6">
      <div className="w-full max-w-xl rounded-xl border border-[var(--line)] bg-[var(--bg-elevated)] p-8">
        <h1 className="mb-4 text-2xl font-semibold tracking-tight text-[var(--text-primary)]">
          GitHub App
        </h1>

        {sp.success && (
          <p className="mb-4 rounded-md border border-[var(--accent)] bg-[var(--accent)]/10 px-3 py-2 text-sm text-[var(--text-primary)]">
            GitHub App created and saved.
          </p>
        )}

        {config ? (
          <>
            <p className="mb-6 text-sm text-[var(--text-muted)]">
              Configured — app slug{" "}
              <code className="text-[var(--text-primary)]">{config.slug}</code>. Customers
              install it from the onboarding wizard.
            </p>
            <p className="text-sm text-[var(--text-muted)]">
              To rotate or replace it, create a new App below — the newest one saved wins.
            </p>
          </>
        ) : (
          <p className="mb-6 text-sm leading-relaxed text-[var(--text-muted)]">
            No GitHub App configured yet. Clicking below takes you to GitHub to review
            permissions and create it — no manual form-filling or env vars required.
          </p>
        )}

        <form action="https://github.com/settings/apps/new" method="post" className="mt-6">
          <input type="hidden" name="manifest" value={manifest} />
          <button
            type="submit"
            className="rounded-md bg-[var(--accent)] px-5 py-2 text-sm font-semibold text-white shadow-sm transition hover:opacity-90"
          >
            {config ? "Create a replacement GitHub App →" : "Create the GitHub App →"}
          </button>
        </form>
      </div>
    </div>
  );
}
