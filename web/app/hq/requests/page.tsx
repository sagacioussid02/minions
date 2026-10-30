import Link from "next/link";
import { HeartbeatDot } from "@/components/HeartbeatDot";
import { RequestsPanel } from "@/components/requests/RequestsPanel";
import { sql } from "@/lib/db";
import { getCurrentTenant } from "@/lib/tenant";
import { listProjects } from "@/lib/tenant-projects";

export const dynamic = "force-dynamic";

async function projectChoices(): Promise<string[]> {
  const tenant = await getCurrentTenant();
  if (!tenant.founder) {
    return (await listProjects(tenant.tenant_id)).map((p) => p.project);
  }
  // The founder's projects live in projects/*.yaml, not tenant_projects: use the
  // ones the org has already produced work for.
  const rows = (await sql()`
    SELECT DISTINCT project FROM decisions WHERE tenant_id = ${tenant.tenant_id} ORDER BY project
  `) as { project: string }[];
  return rows.map((r) => r.project);
}

export default async function RequestsPage() {
  const projects = await projectChoices();
  return (
    <div className="relative flex min-h-screen flex-col">
      <header className="relative flex items-center justify-between border-b border-[var(--line)] px-6 py-3">
        <div className="flex items-center gap-3">
          <Link href="/hq" className="font-mono text-sm tracking-tight text-[var(--accent)]">
            ⌬ minions
          </Link>
          <span className="text-xs text-[var(--text-muted)]">requests</span>
        </div>
        <HeartbeatDot />
      </header>
      <main className="relative mx-auto w-full max-w-3xl flex-1 px-6 py-6">
        <h1 className="mb-1 font-mono text-lg tracking-tight">ask the crew</h1>
        <p className="mb-6 text-sm text-[var(--text-muted)]">
          Describe what you want in plain words. The product owner and principal engineer
          scope it into issues; features wait for your approval before anything is built.
        </p>
        <RequestsPanel projects={projects} />
      </main>
    </div>
  );
}
