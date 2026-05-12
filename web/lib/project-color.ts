/**
 * Stable project → palette index mapping.
 *
 * Sorted alphabetical so the same project always lands on the same color
 * across reloads.
 */

const PALETTE = [
  "var(--project-1)",
  "var(--project-2)",
  "var(--project-3)",
  "var(--project-4)",
  "var(--project-5)",
] as const;

let order: string[] = [];

export function registerProjects(projects: Array<string | null | undefined>): void {
  order = Array.from(new Set(projects.filter((p): p is string => Boolean(p)))).sort();
}

export function colorFor(project: string | null | undefined): string {
  if (!project) return "var(--text-muted)";
  let idx = order.indexOf(project);
  if (idx < 0) {
    // First-time encounter — extend the order list deterministically.
    order = [...order, project].sort();
    idx = order.indexOf(project);
  }
  return PALETTE[idx % PALETTE.length];
}
