"use client";

import { useQuery } from "@tanstack/react-query";
import type { Renewal, SiteHealth, SiteHealthCheck, SiteHealthProject } from "@/lib/schemas";

type Severity = "ok" | "amber" | "red" | "overdue";

const SEVERITY_CLS: Record<Exclude<Severity, "ok">, string> = {
  amber: "bg-[var(--state-warning,#b7791f)]/15 text-[var(--state-warning,#b7791f)]",
  red: "bg-[var(--state-danger)]/15 text-[var(--state-danger)]",
  overdue: "bg-[var(--state-danger)]/20 text-[var(--state-danger)]",
};

function _dueLabel(daysUntil: number): string {
  if (daysUntil < 0) return `${-daysUntil}d overdue`;
  if (daysUntil === 0) return "due today";
  return `in ${daysUntil}d`;
}

async function fetchSiteHealth(): Promise<SiteHealth> {
  const r = await fetch("/api/site-health", { cache: "no-store" });
  if (!r.ok) throw new Error("site-health fetch failed");
  return r.json();
}

export function SiteHealthPanel({ initial }: { initial: SiteHealth }) {
  const q = useQuery({
    queryKey: ["site-health"],
    queryFn: fetchSiteHealth,
    initialData: initial,
    refetchInterval: 30_000,
  });
  const projects = q.data.projects;
  const renewals = q.data.renewals ?? [];

  if (projects.length === 0 && renewals.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-[var(--line)] bg-[var(--bg-surface)] p-6 text-center text-sm text-[var(--text-muted)]">
        No site-health samples yet. Configure{" "}
        <code className="rounded bg-[var(--bg-canvas)] px-1.5 py-0.5 font-mono text-xs">
          deploy.production_url
        </code>{" "}
        + <code className="rounded bg-[var(--bg-canvas)] px-1.5 py-0.5 font-mono text-xs">health_checks</code>{" "}
        in a project manifest, then wait for the next{" "}
        <code className="rounded bg-[var(--bg-canvas)] px-1.5 py-0.5 font-mono text-xs">Site Sentry</code>{" "}
        cron run.
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <RenewalRadar renewals={renewals} />
      {projects.map((p) => (
        <section
          key={p.project}
          className="rounded-xl border border-[var(--line)] bg-[var(--bg-surface)] p-4"
        >
          <header className="mb-3 flex items-center gap-2">
            <StatusDot ok={p.ok} />
            <h2 className="font-semibold text-[var(--text-primary)]">{p.project}</h2>
            <span className="text-xs text-[var(--text-muted)]">
              {p.checks.filter((c) => c.ok).length} / {p.checks.length} checks healthy
            </span>
            <CertBadge project={p} />
          </header>
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-sm">
              <thead>
                <tr className="text-left text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
                  <th className="px-2 py-1.5">Check</th>
                  <th className="px-2 py-1.5">Status</th>
                  <th className="px-2 py-1.5">Latency now</th>
                  <th className="px-2 py-1.5">p50 (24h)</th>
                  <th className="px-2 py-1.5">p99 (24h)</th>
                  <th className="px-2 py-1.5">Uptime (24h)</th>
                  <th className="px-2 py-1.5">Last OK</th>
                  <th className="px-2 py-1.5">Last fail</th>
                  <th className="px-2 py-1.5">Error</th>
                </tr>
              </thead>
              <tbody>
                {p.checks.map((c) => (
                  <CheckRow key={c.check_path} check={c} />
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ))}
    </div>
  );
}

function CheckRow({ check }: { check: SiteHealthCheck }) {
  return (
    <tr className="border-t border-[var(--line)] text-[var(--text-primary)]">
      <td className="px-2 py-1.5 font-mono text-xs">{check.check_path}</td>
      <td className="px-2 py-1.5">
        <StatusPill ok={check.ok} statusCode={check.status_code} />
      </td>
      <td className="px-2 py-1.5 font-mono text-xs tabular-nums">
        {check.latency_ms != null ? `${check.latency_ms} ms` : "—"}
      </td>
      <td className="px-2 py-1.5 font-mono text-xs tabular-nums text-[var(--text-muted)]">
        {check.samples_24h > 0 ? `${check.p50_ms_24h} ms` : "—"}
      </td>
      <td className="px-2 py-1.5 font-mono text-xs tabular-nums text-[var(--text-muted)]">
        {check.samples_24h > 0 ? `${check.p99_ms_24h} ms` : "—"}
      </td>
      <td className="px-2 py-1.5 font-mono text-xs tabular-nums text-[var(--text-muted)]">
        {check.samples_24h > 0 ? `${(check.uptime_24h * 100).toFixed(1)}%` : "—"}
      </td>
      <td className="px-2 py-1.5 text-xs text-[var(--text-muted)]">
        {check.last_ok_at ? _ago(check.last_ok_at) : "never"}
      </td>
      <td className="px-2 py-1.5 text-xs text-[var(--text-muted)]">
        {check.last_failed_at ? _ago(check.last_failed_at) : "never"}
      </td>
      <td className="px-2 py-1.5 max-w-[280px] truncate text-xs text-[var(--state-danger)]">
        {check.error || ""}
      </td>
    </tr>
  );
}

function CertBadge({ project }: { project: SiteHealthProject }) {
  if (project.cert_expires_at === null || project.cert_days_until === null) return null;
  const sev = project.cert_severity;
  const label = `TLS ${_dueLabel(project.cert_days_until)}`;
  if (sev === "ok" || sev === null) {
    return (
      <span className="ml-auto rounded px-1.5 py-0.5 text-[10px] font-medium text-[var(--text-muted)]">
        🔒 cert {_dueLabel(project.cert_days_until)}
      </span>
    );
  }
  return (
    <span
      className={`ml-auto rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${SEVERITY_CLS[sev]}`}
      title={`Certificate expires ${project.cert_expires_at}`}
    >
      🔒 {label}
    </span>
  );
}

function RenewalRadar({ renewals }: { renewals: Renewal[] }) {
  if (renewals.length === 0) return null;
  // Surface anything not fully "ok" first; keep the section quiet otherwise.
  const flagged = renewals.filter((r) => r.severity !== "ok");
  const rest = renewals.filter((r) => r.severity === "ok");
  const ordered = [...flagged, ...rest];
  return (
    <section className="rounded-xl border border-[var(--line)] bg-[var(--bg-surface)] p-4">
      <header className="mb-3 flex items-center gap-2">
        <h2 className="font-semibold text-[var(--text-primary)]">Renewal radar</h2>
        <span className="text-xs text-[var(--text-muted)]">
          {flagged.length > 0 ? `${flagged.length} due soon` : "all clear"} · licenses &amp;
          credential rotations (dates only)
        </span>
      </header>
      <ul className="space-y-1.5">
        {ordered.map((r) => (
          <li
            key={`${r.project}:${r.kind}:${r.name}`}
            className="flex items-center gap-2 text-sm text-[var(--text-primary)]"
          >
            <span className="text-xs" title={r.kind}>
              {r.kind === "secret_rotation" ? "🔑" : "📄"}
            </span>
            <span className="font-medium">
              {r.url ? (
                <a href={r.url} target="_blank" rel="noreferrer" className="underline decoration-dotted">
                  {r.name}
                </a>
              ) : (
                r.name
              )}
            </span>
            <span className="text-xs text-[var(--text-muted)]">{r.project}</span>
            {r.note && <span className="text-xs text-[var(--text-muted)]">· {r.note}</span>}
            <span className="ml-auto font-mono text-xs tabular-nums text-[var(--text-muted)]">
              {r.due}
            </span>
            <RenewalPill severity={r.severity} daysUntil={r.days_until} />
          </li>
        ))}
      </ul>
    </section>
  );
}

function RenewalPill({ severity, daysUntil }: { severity: Severity; daysUntil: number }) {
  if (severity === "ok") {
    return (
      <span className="rounded px-1.5 py-0.5 text-[10px] font-medium text-[var(--text-muted)]">
        {_dueLabel(daysUntil)}
      </span>
    );
  }
  return (
    <span
      className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${SEVERITY_CLS[severity]}`}
    >
      {_dueLabel(daysUntil)}
    </span>
  );
}

function StatusDot({ ok }: { ok: boolean }) {
  return (
    <span
      className="inline-block h-2.5 w-2.5 rounded-full"
      style={{
        backgroundColor: ok ? "var(--state-success)" : "var(--state-danger)",
        boxShadow: `0 0 0 3px ${ok ? "var(--state-success)" : "var(--state-danger)"}33`,
      }}
    />
  );
}

function StatusPill({ ok, statusCode }: { ok: boolean; statusCode: number | null }) {
  const cls = ok
    ? "bg-[var(--state-success)]/15 text-[var(--state-success)]"
    : "bg-[var(--state-danger)]/15 text-[var(--state-danger)]";
  return (
    <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${cls}`}>
      {ok ? "ok" : "fail"}
      {statusCode != null && ` · ${statusCode}`}
    </span>
  );
}

function _ago(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  if (ms < 0) return "just now";
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}
