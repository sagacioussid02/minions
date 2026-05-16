import { Avatar } from "@/components/Avatar";
import { type HeroEvent as HeroEventT } from "@/lib/schemas";
import { iconFor, prettyRole } from "@/lib/roles";
import { formatDistanceToNowStrict } from "date-fns";

/**
 * The single-line "what just happened" hero strip at the top of the page.
 *
 * Server-rendered. Pulsing avatar + one natural-language sentence + relative
 * timestamp. When the event is fresh (<2 min) the avatar gets a halo + pulse
 * animation; older events render quietly.
 */
export function HeroEvent({ event }: { event: HeroEventT }) {
  if (!event) {
    return (
      <div className="rounded-2xl border border-[var(--line)] bg-[var(--bg-surface)] px-5 py-3 text-sm text-[var(--text-muted)]">
        Quiet floor. No meaningful events recorded yet.
      </div>
    );
  }

  const ageMs = new Date().getTime() - new Date(event.ts).getTime();
  const isLive = ageMs < 2 * 60 * 1000;
  const ageLabel = formatDistanceToNowStrict(new Date(event.ts), { addSuffix: true });
  const tierColor = `var(--color-role-${event.role_tier})`;

  return (
    <div className="relative overflow-hidden rounded-2xl border border-[var(--line)] bg-gradient-to-b from-[var(--bg-elevated)] to-[var(--bg-surface)] px-5 py-4">
      {/* Soft accent line on the left edge */}
      <span
        className="pointer-events-none absolute inset-y-3 left-0 w-0.5 rounded-full"
        style={{ background: tierColor, opacity: isLive ? 0.95 : 0.55 }}
      />
      <div className="flex items-center gap-4">
        <div className={`relative ${isLive ? "pulse-halo" : ""}`}>
          <Avatar seed={event.avatar_seed} size={48} ring={tierColor} />
        </div>
        <div className="flex flex-1 flex-col gap-0.5">
          <div className="flex items-center gap-2 text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
            <span className="font-mono text-base leading-none" style={{ color: tierColor }} aria-hidden>
              {iconFor(event.role_tier)}
            </span>
            <span>{prettyRole(event.role ?? "system")}</span>
            {event.project && <span>· {event.project}</span>}
            <span className="ml-auto">{ageLabel}</span>
          </div>
          <div className="text-base font-medium tracking-tight text-[var(--text-primary)]">
            {event.sentence}
          </div>
        </div>
        {event.deep_link_href && (
          event.deep_link_href.startsWith("http") ? (
            <a
              href={event.deep_link_href}
              target="_blank"
              rel="noreferrer"
              className="rounded-md border border-[var(--line)] px-2.5 py-1 font-mono text-xs text-[var(--text-muted)] hover:border-[var(--accent)]/40 hover:text-[var(--text-primary)]"
            >
              {event.deep_link_label ?? "open"}
            </a>
          ) : (
            <a
              href={event.deep_link_href}
              className="rounded-md border border-[var(--line)] px-2.5 py-1 font-mono text-xs text-[var(--text-muted)] hover:border-[var(--accent)]/40 hover:text-[var(--text-primary)]"
            >
              {event.deep_link_label ?? "open"}
            </a>
          )
        )}
      </div>
    </div>
  );
}
