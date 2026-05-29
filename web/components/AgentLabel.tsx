/**
 * Stacked agent identity — name primary, role secondary. Used on cards,
 * round-table seats, and anywhere there's room for two lines.
 *
 * For single-line / inline contexts use `agentLabel(name, role)` from
 * `@/lib/roles` instead.
 */

import { roleShortLabel } from "@/lib/roles";

export function AgentLabel({
  displayName,
  role,
  size = "md",
  className,
}: {
  displayName: string | null | undefined;
  role: string;
  size?: "sm" | "md" | "lg";
  className?: string;
}) {
  const name = (displayName ?? "").trim();
  const short = roleShortLabel(role);
  const hasName = name.length > 0;

  const nameClass =
    size === "lg"
      ? "text-base font-semibold"
      : size === "sm"
        ? "text-xs font-semibold"
        : "text-sm font-semibold";
  const roleClass =
    size === "lg" ? "text-[11px]" : size === "sm" ? "text-[9px]" : "text-[10px]";

  return (
    <div className={`min-w-0 leading-tight ${className ?? ""}`}>
      <div className={`truncate text-[var(--text-primary)] ${nameClass}`}>
        {hasName ? name : short}
      </div>
      {hasName && (
        <div
          className={`truncate uppercase tracking-wider text-[var(--text-muted)] ${roleClass}`}
        >
          {short}
        </div>
      )}
    </div>
  );
}
