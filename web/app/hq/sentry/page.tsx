import Link from "next/link";
import { HeartbeatDot } from "@/components/HeartbeatDot";
import { SiteHealthPanel } from "@/components/sentry/SiteHealthPanel";
import { listSiteHealth } from "@/lib/queries";

export const dynamic = "force-dynamic";

export default async function SentryPage() {
  const siteHealth = await listSiteHealth();
  const totalProjects = siteHealth.projects.length;
  const healthy = siteHealth.projects.filter((p) => p.ok).length;

  return (
    <div className="relative flex min-h-screen flex-col">
      <header className="relative flex items-center justify-between border-b border-[var(--line)] px-6 py-3">
        <div className="flex items-center gap-3">
          <Link href="/hq" className="font-mono text-sm tracking-tight text-[var(--accent)]">
            ⌬ minions
          </Link>
          <span className="text-xs text-[var(--text-muted)]">sentry</span>
          <span className="text-xs text-[var(--text-muted)]">·</span>
          <span className="text-xs text-[var(--text-muted)]">
            {healthy} / {totalProjects} projects green
          </span>
        </div>
        <HeartbeatDot />
      </header>
      <main className="flex-1 overflow-y-auto p-6">
        <div className="mx-auto max-w-6xl space-y-4">
          <section>
            <h1 className="mb-1 text-lg font-semibold text-[var(--text-primary)]">
              Site health
            </h1>
            <p className="text-sm text-[var(--text-muted)]">
              Continuous synthetic monitoring of each project&apos;s declared{" "}
              <code className="rounded bg-[var(--bg-canvas)] px-1.5 py-0.5 font-mono text-xs">
                deploy.production_url
              </code>{" "}
              + health checks. Probed every 10 min by the Site Sentry cron.
            </p>
          </section>
          <SiteHealthPanel initial={siteHealth} />
        </div>
      </main>
    </div>
  );
}
