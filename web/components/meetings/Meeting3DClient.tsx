"use client";

import dynamic from "next/dynamic";
import { useSyncExternalStore } from "react";
import type { MeetingDetail } from "@/lib/schemas";

/**
 * Client gate for the 3D route. Performs the WebGL + reduced-motion
 * capability check, then lazy-loads the heavy `Meeting3D` island
 * (`ssr:false`) so three / R3F / drei stay out of the shared bundle and
 * never run during SSR (WebGL needs a browser).
 */

const Meeting3D = dynamic(() => import("./Meeting3D"), {
  ssr: false,
  loading: () => (
    <div className="flex h-[70vh] items-center justify-center text-xs text-[var(--text-muted)]">
      Loading 3D scene…
    </div>
  ),
});

type Capability = "checking" | "ok" | "reduced-motion" | "no-webgl";

let cachedCapability: Exclude<Capability, "checking"> | null = null;

function detectCapability(): Exclude<Capability, "checking"> {
  if (cachedCapability) return cachedCapability;
  if (window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches) {
    cachedCapability = "reduced-motion";
    return cachedCapability;
  }
  try {
    const canvas = document.createElement("canvas");
    const gl =
      canvas.getContext("webgl2") ||
      canvas.getContext("webgl") ||
      canvas.getContext("experimental-webgl");
    cachedCapability = gl ? "ok" : "no-webgl";
  } catch {
    cachedCapability = "no-webgl";
  }
  return cachedCapability;
}

const noopSubscribe = () => () => {};

export function Meeting3DClient({
  initial,
  backHref,
}: {
  initial: MeetingDetail;
  backHref: string;
}) {
  // "checking" during SSR + first hydration paint; the client snapshot
  // resolves the real capability without a setState-in-effect cascade.
  const cap = useSyncExternalStore<Capability>(
    noopSubscribe,
    detectCapability,
    () => "checking",
  );

  if (cap === "checking") {
    return (
      <div className="flex h-[70vh] items-center justify-center text-xs text-[var(--text-muted)]">
        Checking display capabilities…
      </div>
    );
  }

  if (cap === "no-webgl") {
    return <Fallback backHref={backHref} reason="WebGL isn’t available in this browser." />;
  }

  // Reduced motion: still render the scene, but static (no halo pulse, no
  // orbit drag) — handled inside Meeting3D via the reducedMotion flag.
  return (
    <Meeting3D initial={initial} backHref={backHref} reducedMotion={cap === "reduced-motion"} />
  );
}

function Fallback({ backHref, reason }: { backHref: string; reason: string }) {
  return (
    <div className="flex h-[70vh] flex-col items-center justify-center gap-3 text-center">
      <p className="max-w-sm text-sm text-[var(--text-muted)]">{reason}</p>
      <a
        href={backHref}
        className="rounded border border-[var(--accent)] bg-[var(--accent)]/10 px-3 py-1 text-sm font-medium text-[var(--accent)] hover:bg-[var(--accent)]/20"
      >
        ← Open the 2D meeting view
      </a>
    </div>
  );
}
