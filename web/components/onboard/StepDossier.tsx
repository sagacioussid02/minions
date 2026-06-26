"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { OnboardShell } from "./OnboardShell";

// Placeholder for P8 (dossier Q&A + first-sprint kickoff). For now, finishing
// marks onboarding complete and opens HQ.
export function StepDossier() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);

  async function finish() {
    setBusy(true);
    try {
      await fetch("/api/onboard/save-step", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ step: "complete", payload: {} }),
      });
      router.push("/hq");
      router.refresh();
    } finally {
      setBusy(false);
    }
  }

  return (
    <OnboardShell stepIndex={2} title="Tell the agents about each project">
      <p className="mb-6 text-sm leading-relaxed text-[var(--text-muted)]">
        Next we&apos;ll ask a few questions per project (purpose, users, top
        features) and kick off your first sprint planning. That arrives in P8 —
        for now, finish setup and open HQ.
      </p>
      <div className="flex justify-end">
        <button
          type="button"
          onClick={finish}
          disabled={busy}
          className="rounded-md bg-[var(--accent)] px-5 py-2 text-sm font-semibold text-white shadow-sm transition hover:opacity-90 disabled:opacity-50"
        >
          Finish &amp; open HQ
        </button>
      </div>
    </OnboardShell>
  );
}
