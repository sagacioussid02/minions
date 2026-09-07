"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { OnboardShell } from "./OnboardShell";
import type { GhRepo } from "@/lib/github-app";

const btnPrimary =
  "rounded-md bg-[var(--accent)] px-5 py-2 text-sm font-semibold text-white shadow-sm transition hover:opacity-90 disabled:opacity-50";
const link = "text-sm font-medium text-[var(--accent)] hover:underline";

export function StepRepos({
  installUrl,
  repos,
  cap,
  selected,
}: {
  installUrl: string | null;
  repos: GhRepo[] | null;
  cap: number;
  selected: string[];
}) {
  const router = useRouter();
  const [picked, setPicked] = useState<Set<string>>(new Set(selected));
  const [busy, setBusy] = useState(false);

  if (!installUrl) {
    return (
      <OnboardShell stepIndex={0} title="Connect your repositories">
        <p className="text-sm text-[var(--text-muted)]">
          The GitHub App isn&apos;t configured yet. Set{" "}
          <code>MINIONS_GITHUB_APP_*</code> in the environment.
        </p>
      </OnboardShell>
    );
  }

  if (repos === null) {
    return (
      <OnboardShell stepIndex={0} title="Connect your repositories">
        <p className="mb-6 text-sm leading-relaxed text-[var(--text-muted)]">
          Install the minions GitHub App and choose the repositories it may work
          on. You can change this anytime from GitHub.
        </p>
        <a href={installUrl} className={btnPrimary}>
          Install the GitHub App →
        </a>
      </OnboardShell>
    );
  }

  function toggle(fullName: string) {
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(fullName)) next.delete(fullName);
      else if (next.size < cap) next.add(fullName);
      return next;
    });
  }

  async function cont() {
    setBusy(true);
    try {
      const chosen = (repos ?? [])
        .filter((r) => picked.has(r.full_name))
        .map((r) => ({ full_name: r.full_name, default_branch: r.default_branch }));
      const res = await fetch("/api/onboard/repos", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ repos: chosen }),
      });
      if (res.ok) router.refresh();
    } finally {
      setBusy(false);
    }
  }

  return (
    <OnboardShell stepIndex={0} title="Choose repositories">
      <p className="mb-4 text-sm text-[var(--text-muted)]">
        Pick up to {cap}. ({picked.size}/{cap} selected)
      </p>
      {repos.length === 0 ? (
        <p className="mb-6 text-sm text-[var(--text-muted)]">
          No repositories in this installation.{" "}
          <a href={installUrl} className={link}>
            Add some on GitHub →
          </a>
        </p>
      ) : (
        <div className="mb-6 max-h-72 space-y-1 overflow-y-auto">
          {repos.map((r) => {
            const checked = picked.has(r.full_name);
            const disabled = !checked && picked.size >= cap;
            return (
              <label
                key={r.id}
                className={`flex items-center gap-3 rounded-md border px-3 py-2 text-sm ${
                  checked
                    ? "border-[var(--accent)] bg-[var(--accent)]/10"
                    : "border-[var(--line)]"
                } ${disabled ? "opacity-40" : "cursor-pointer"}`}
              >
                <input
                  type="checkbox"
                  checked={checked}
                  disabled={disabled}
                  onChange={() => toggle(r.full_name)}
                />
                <span className="text-[var(--text-primary)]">{r.full_name}</span>
                {r.private && (
                  <span className="ml-auto text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
                    private
                  </span>
                )}
              </label>
            );
          })}
        </div>
      )}
      <div className="flex items-center justify-between">
        <a href={installUrl} className={link}>
          Manage on GitHub
        </a>
        <button
          type="button"
          onClick={cont}
          disabled={busy || picked.size === 0}
          className={btnPrimary}
        >
          Continue
        </button>
      </div>
    </OnboardShell>
  );
}
