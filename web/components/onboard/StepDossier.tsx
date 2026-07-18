"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { OnboardShell } from "./OnboardShell";
import type { TenantProjectSummary } from "@/lib/tenant-projects";

type Answers = {
  project: string;
  purpose: string;
  primaryUsers: string;
  topFeatures: string;
  nonGoals: string;
};

const input =
  "w-full rounded-md border border-[var(--line)] bg-[var(--bg-surface)] px-2 py-1.5 text-sm text-[var(--text-primary)]";
const labelCls = "mb-1 block text-xs uppercase tracking-wider text-[var(--text-muted)]";

export function StepDossier({
  projects,
  isSandbox,
}: {
  projects: TenantProjectSummary[];
  isSandbox: boolean;
}) {
  const router = useRouter();
  const [answers, setAnswers] = useState<Answers[]>(
    projects.map((p) => ({
      project: p.project,
      purpose: p.description ?? "",
      primaryUsers: "",
      topFeatures: "",
      nonGoals: "",
    })),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function update(i: number, patch: Partial<Answers>) {
    setAnswers((a) => a.map((x, j) => (j === i ? { ...x, ...patch } : x)));
  }

  async function finish() {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/onboard/dossier", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ answers }),
      });
      if (res.ok) {
        router.push("/hq?welcome=1");
        router.refresh();
        return;
      }
      setError("Could not save — try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <OnboardShell stepIndex={2} title="Tell the agents about each project">
      <p className="mb-2 text-sm leading-relaxed text-[var(--text-muted)]">
        A few questions per project, then the crew plans your first sprint —
        no waiting for the weekly cadence.
      </p>
      {isSandbox && (
        <p className="mb-6 text-xs text-[var(--text-muted)]">
          You&apos;re on the free sandbox — one project, a small one-time
          budget, real planning and a real draft PR. Want more projects or a
          bigger budget? Watch the repo for the hosted version.
        </p>
      )}
      <div className="mb-6 space-y-5">
        {answers.map((a, i) => (
          <div key={a.project} className="rounded-lg border border-[var(--line)] p-4">
            <div className="mb-3 font-mono text-xs text-[var(--accent)]">
              {projects[i]?.name ?? a.project}
            </div>
            <div className="space-y-3">
              <div>
                <label className={labelCls}>Purpose — what is this project for?</label>
                <input
                  className={input}
                  value={a.purpose}
                  onChange={(e) => update(i, { purpose: e.target.value })}
                />
              </div>
              <div>
                <label className={labelCls}>Primary users</label>
                <input
                  className={input}
                  value={a.primaryUsers}
                  placeholder="Who uses this day to day"
                  onChange={(e) => update(i, { primaryUsers: e.target.value })}
                />
              </div>
              <div>
                <label className={labelCls}>Top features for this month</label>
                <input
                  className={input}
                  value={a.topFeatures}
                  placeholder="Comma-separated"
                  onChange={(e) => update(i, { topFeatures: e.target.value })}
                />
              </div>
              <div>
                <label className={labelCls}>Non-goals (what to explicitly avoid)</label>
                <input
                  className={input}
                  value={a.nonGoals}
                  onChange={(e) => update(i, { nonGoals: e.target.value })}
                />
              </div>
            </div>
          </div>
        ))}
      </div>
      {error && <p className="mb-3 text-sm text-[var(--state-danger)]">{error}</p>}
      <div className="flex justify-end">
        <button
          type="button"
          onClick={finish}
          disabled={busy}
          className="rounded-md bg-[var(--accent)] px-5 py-2 text-sm font-semibold text-white shadow-sm transition hover:opacity-90 disabled:opacity-50"
        >
          {busy ? "Kicking off your first sprint…" : "Finish & plan my first sprint"}
        </button>
      </div>
    </OnboardShell>
  );
}
