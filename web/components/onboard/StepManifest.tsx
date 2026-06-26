"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { OnboardShell } from "./OnboardShell";
import type { PickedRepo } from "@/lib/tenant-projects";

type Form = {
  repoFullName: string;
  defaultBranch: string;
  name: string;
  description: string;
  weeklyBudgetUsd: number;
  monthlyBudgetUsd: number;
};

const input =
  "w-full rounded-md border border-[var(--line)] bg-[var(--bg-surface)] px-2 py-1.5 text-sm text-[var(--text-primary)]";
const labelCls = "mb-1 block text-xs uppercase tracking-wider text-[var(--text-muted)]";

export function StepManifest({ repos }: { repos: PickedRepo[] }) {
  const router = useRouter();
  const [forms, setForms] = useState<Form[]>(
    repos.map((r) => ({
      repoFullName: r.full_name,
      defaultBranch: r.default_branch || "main",
      name: r.full_name.split("/")[1] ?? r.full_name,
      description: "",
      weeklyBudgetUsd: 25,
      monthlyBudgetUsd: 100,
    })),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function update(i: number, patch: Partial<Form>) {
    setForms((f) => f.map((x, j) => (j === i ? { ...x, ...patch } : x)));
  }

  async function cont() {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/onboard/projects", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ projects: forms }),
      });
      if (res.ok) {
        router.refresh();
        return;
      }
      const j = await res.json().catch(() => ({}));
      setError(j.error === "project_cap_reached" ? `Project cap reached (${j.cap}).` : "Could not save projects.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <OnboardShell stepIndex={1} title="Configure each project">
      <div className="mb-6 space-y-5">
        {forms.map((f, i) => (
          <div key={f.repoFullName} className="rounded-lg border border-[var(--line)] p-4">
            <div className="mb-3 font-mono text-xs text-[var(--accent)]">{f.repoFullName}</div>
            <div className="space-y-3">
              <div>
                <label className={labelCls}>Project name</label>
                <input
                  className={input}
                  value={f.name}
                  onChange={(e) => update(i, { name: e.target.value })}
                />
              </div>
              <div>
                <label className={labelCls}>One-line description</label>
                <input
                  className={input}
                  value={f.description}
                  placeholder="What this project is"
                  onChange={(e) => update(i, { description: e.target.value })}
                />
              </div>
              <div className="flex gap-3">
                <div className="flex-1">
                  <label className={labelCls}>Weekly budget ($)</label>
                  <input
                    type="number"
                    min={0}
                    className={input}
                    value={f.weeklyBudgetUsd}
                    onChange={(e) => update(i, { weeklyBudgetUsd: Number(e.target.value) })}
                  />
                </div>
                <div className="flex-1">
                  <label className={labelCls}>Monthly budget ($)</label>
                  <input
                    type="number"
                    min={0}
                    className={input}
                    value={f.monthlyBudgetUsd}
                    onChange={(e) => update(i, { monthlyBudgetUsd: Number(e.target.value) })}
                  />
                </div>
              </div>
              <div className="text-[11px] text-[var(--text-muted)]">
                default branch: <code>{f.defaultBranch}</code>
              </div>
            </div>
          </div>
        ))}
      </div>
      {error && <p className="mb-3 text-sm text-[var(--state-danger)]">{error}</p>}
      <div className="flex justify-end">
        <button
          type="button"
          onClick={cont}
          disabled={busy}
          className="rounded-md bg-[var(--accent)] px-5 py-2 text-sm font-semibold text-white shadow-sm transition hover:opacity-90 disabled:opacity-50"
        >
          Continue
        </button>
      </div>
    </OnboardShell>
  );
}
