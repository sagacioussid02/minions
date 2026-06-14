"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

// Skeleton wizard (P6). Real fields land in P7 (repos + manifest) and P8
// (dossier + first-sprint kickoff). For now each step is a placeholder; the
// step is persisted on every transition so closing the tab resumes here.
const STEPS = ["repos", "manifest", "dossier"] as const;
type Step = (typeof STEPS)[number];

const COPY: Record<Step, { title: string; body: string }> = {
  repos: {
    title: "Connect your repositories",
    body: "You'll install the minions GitHub App and pick up to 2 repos for it to work on. (Wired up in P7.)",
  },
  manifest: {
    title: "Configure each project",
    body: "Project name, default branch, weekly/monthly budget caps, planning day. (Wired up in P7.)",
  },
  dossier: {
    title: "Tell the agents about each project",
    body: "One-sentence purpose, who the users are, and the top features for the next month — then minions plans your first sprint. (Wired up in P8.)",
  },
};

function normalize(step: string): Step {
  return (STEPS as readonly string[]).includes(step) ? (step as Step) : "repos";
}

export function OnboardWizard({ initialStep }: { initialStep: string }) {
  const router = useRouter();
  const [step, setStep] = useState<Step>(normalize(initialStep));
  const [saving, setSaving] = useState(false);
  const idx = STEPS.indexOf(step);

  async function persist(nextStep: Step | "complete") {
    setSaving(true);
    try {
      await fetch("/api/onboard/save-step", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ step: nextStep, payload: {} }),
      });
    } finally {
      setSaving(false);
    }
  }

  async function next() {
    if (idx < STEPS.length - 1) {
      const ns = STEPS[idx + 1];
      await persist(ns);
      setStep(ns);
    } else {
      await persist("complete");
      router.push("/hq");
      router.refresh();
    }
  }

  function back() {
    if (idx > 0) setStep(STEPS[idx - 1]);
  }

  const copy = COPY[step];

  return (
    <div className="flex min-h-screen items-center justify-center bg-[var(--bg-canvas)] p-6">
      <div className="w-full max-w-xl rounded-xl border border-[var(--line)] bg-[var(--bg-elevated)] p-8">
        <div className="mb-6 flex items-center gap-2">
          {STEPS.map((s, i) => (
            <div
              key={s}
              className={`h-1.5 flex-1 rounded-full ${
                i <= idx ? "bg-[var(--accent)]" : "bg-[var(--line)]"
              }`}
            />
          ))}
        </div>
        <div className="mb-1 font-mono text-xs uppercase tracking-wider text-[var(--text-muted)]">
          Step {idx + 1} of {STEPS.length}
        </div>
        <h1 className="mb-3 text-2xl font-semibold tracking-tight text-[var(--text-primary)]">
          {copy.title}
        </h1>
        <p className="mb-8 text-sm leading-relaxed text-[var(--text-muted)]">
          {copy.body}
        </p>
        <div className="flex items-center justify-between">
          <button
            type="button"
            onClick={back}
            disabled={idx === 0 || saving}
            className="rounded-md border border-[var(--line)] px-4 py-2 text-sm font-medium text-[var(--text-muted)] disabled:opacity-40 hover:border-[var(--accent)]/40"
          >
            Back
          </button>
          <button
            type="button"
            onClick={next}
            disabled={saving}
            className="rounded-md bg-[var(--accent)] px-5 py-2 text-sm font-semibold text-white shadow-sm transition hover:opacity-90 disabled:opacity-60"
          >
            {idx < STEPS.length - 1 ? "Continue" : "Finish & open HQ"}
          </button>
        </div>
      </div>
    </div>
  );
}
