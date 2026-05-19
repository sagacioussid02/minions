"use client";

import { formatDistanceToNowStrict } from "date-fns";
import { Avatar } from "@/components/Avatar";
import { deepLinks } from "@/lib/activity-renderer";
import { agentSeedFor, prettyRole, tierFor } from "@/lib/roles";
import { type ActivityEvent } from "@/lib/schemas";
import { sentenceOnly } from "@/lib/stage-sentences";

export function ChatLine({
  event,
  compact = false,
}: {
  event: ActivityEvent;
  compact?: boolean;
}) {
  const role = event.role ?? event.crew ?? "system";
  const tier = tierFor(role);
  const links = deepLinks(event);
  const project = event.project ?? "company";
  const sentence = sentenceOnly(event);

  return (
    <li className={`row-in flex gap-3 ${compact ? "px-4 py-3" : "px-5 py-4"}`}>
      <Avatar
        seed={agentSeedFor(role, event.project)}
        size={compact ? 30 : 38}
        ring={`var(--color-role-${tier})`}
      />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium text-[var(--text-primary)]">{prettyRole(role)}</span>
          <span
            className="rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-white"
            style={{ backgroundColor: `var(--color-role-${tier})` }}
          >
            {project}
          </span>
          <span className="text-xs text-[var(--text-muted)]">
            {formatDistanceToNowStrict(new Date(event.ts), { addSuffix: true })}
          </span>
        </div>
        <p className="mt-1 text-sm leading-6 text-[var(--text-primary)]">{sentence}</p>
        {links.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {links.map((link, index) =>
              link.href.startsWith("http") ? (
                <a
                  key={`${link.href}-${index}`}
                  href={link.href}
                  target="_blank"
                  rel="noreferrer"
                  className="rounded border border-[var(--line)] px-2 py-0.5 font-mono text-xs text-[var(--text-muted)] hover:border-[var(--accent)]/40 hover:text-[var(--text-primary)]"
                >
                  {link.label}
                </a>
              ) : (
                <a
                  key={`${link.href}-${index}`}
                  href={link.href}
                  className="rounded border border-[var(--line)] px-2 py-0.5 font-mono text-xs text-[var(--text-muted)] hover:border-[var(--accent)]/40 hover:text-[var(--text-primary)]"
                >
                  {link.label}
                </a>
              ),
            )}
          </div>
        )}
      </div>
    </li>
  );
}
