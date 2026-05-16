/**
 * Deterministic agent avatar — `bottts` style.
 *
 * Each agent gets a unique tiny-robot face seeded by its stable id
 * (`role@project`). Rendered as inline SVG so it scales crisply and ships
 * with zero runtime image requests. Server-renderable.
 *
 * Color hint: pass a CSS color (e.g. the agent's role-tier var) and the
 * bottts background ring tints to match. Keeps the gallery cohesive instead
 * of looking like 60 random robots.
 */

import { createAvatar } from "@dicebear/core";
import { bottts } from "@dicebear/collection";

export function Avatar({
  seed,
  size = 40,
  ring,
  className,
}: {
  seed: string;
  size?: number;
  ring?: string; // CSS color for a 1.5px halo, e.g. `var(--color-role-engineering)`
  className?: string;
}) {
  const svg = createAvatar(bottts, {
    seed,
    size,
    radius: 50, // fully round
    backgroundType: ["solid"],
    backgroundColor: ["1e2532"], // matches `--bg-elevated`-ish (slate-800)
  }).toString();

  return (
    <span
      className={`inline-flex shrink-0 items-center justify-center ${className ?? ""}`}
      style={{
        width: size,
        height: size,
        borderRadius: "50%",
        boxShadow: ring ? `0 0 0 1.5px ${ring}` : undefined,
        overflow: "hidden",
      }}
      // SVG is generated client-safe, deterministic per seed.
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
}
