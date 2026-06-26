import Link from "next/link";
import type { MeetingSummary } from "@/lib/schemas";
import { RoundTable } from "./RoundTable";

/**
 * Bare-bones meetings list — PR 1 of living-org-spaces Surface A.
 *
 * Shows a card per crew run with: ritual label, agenda, status, project,
 * the seat roster, latest spoken line. The round-table SVG visualization
 * lands in PR 2; this page proves the data flows and gives the operator
 * a usable surface in the meantime.
 */
export function MeetingsList({ meetings }: { meetings: MeetingSummary[] }) {
  if (meetings.length === 0) {
    return (
      <div className="rounded-lg border border-[var(--line)] p-8 text-center text-sm text-[var(--text-muted)]">
        No meetings in the last 7 days. Run{" "}
        <code className="rounded bg-[var(--bg-canvas)] px-1 py-0.5">
          minions plan &lt;project&gt; --no-dry-run
        </code>{" "}
        to kick off a planning crew.
      </div>
    );
  }

  // Split by ritual type (group round-table vs solo focused work) so group
  // rituals always get their own section — even while live — and aren't
  // buried by frequent engineer runs. Live meetings sort to the top.
  const liveFirst = (a: MeetingSummary, b: MeetingSummary) =>
    Number(b.status === "in_progress") - Number(a.status === "in_progress");
  const groupRituals = meetings.filter((m) => m.multi_agent).sort(liveFirst);
  const focusedWork = meetings.filter((m) => !m.multi_agent).sort(liveFirst);

  return (
    <div className="space-y-8">
      <MeetingSection title="Group rituals" meetings={groupRituals} accent />
      <MeetingSection title="Focused work" meetings={focusedWork} />
    </div>
  );
}

function MeetingSection({
  title,
  meetings,
  accent = false,
}: {
  title: string;
  meetings: MeetingSummary[];
  accent?: boolean;
}) {
  if (meetings.length === 0) return null;
  return (
    <section>
      <h2
        className={`mb-3 text-xs font-semibold uppercase tracking-wider ${
          accent ? "text-[var(--accent)]" : "text-[var(--text-muted)]"
        }`}
      >
        {title} ({meetings.length})
      </h2>
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        {meetings.map((m) => (
          <MeetingCard key={m.run_id} meeting={m} live={m.status === "in_progress"} />
        ))}
      </div>
    </section>
  );
}

function MeetingCard({ meeting, live }: { meeting: MeetingSummary; live: boolean }) {
  const accent = live
    ? "border-[var(--accent)]/40"
    : "border-[var(--line)]";
  return (
    <Link
      href={`/hq/meetings/${meeting.run_id}`}
      className={`block rounded-lg border ${accent} bg-[var(--bg-surface)] p-4 transition hover:border-[var(--accent)]/60 hover:bg-[var(--bg-elevated)]`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-[var(--text-primary)]">
              {meeting.ritual_label}
            </span>
            {meeting.project && (
              <span className="rounded bg-[var(--bg-canvas)] px-1.5 py-0.5 font-mono text-[10px] text-[var(--text-muted)]">
                {meeting.project}
              </span>
            )}
            <span
              className={`rounded px-1.5 py-0.5 text-[10px] uppercase tracking-wider ${
                meeting.status === "in_progress"
                  ? "bg-[var(--state-success)]/15 text-[var(--state-success)]"
                  : meeting.status === "failed"
                    ? "bg-[var(--state-danger)]/15 text-[var(--state-danger)]"
                    : "bg-[var(--bg-canvas)] text-[var(--text-muted)]"
              }`}
            >
              {meeting.status === "in_progress" ? "live" : meeting.status}
            </span>
            {!meeting.multi_agent && (
              <span className="rounded bg-[var(--bg-canvas)] px-1.5 py-0.5 text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
                solo
              </span>
            )}
          </div>
          <p className="mt-1 text-xs text-[var(--text-muted)]">{meeting.ritual_agenda}</p>
        </div>
        <span className="shrink-0 font-mono text-[10px] text-[var(--text-muted)]">
          {meeting.total_turns} turn{meeting.total_turns === 1 ? "" : "s"}
        </span>
      </div>

      <div className="mt-3 flex flex-col items-center gap-2 sm:flex-row sm:items-start">
        <div className="w-full max-w-[220px]">
          <RoundTable seats={meeting.seats} multiAgent={meeting.multi_agent} size="sm" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap gap-1.5">
            {meeting.seats.map((seat) => (
              <span
                key={`${meeting.run_id}-${seat.agent_role}`}
                className={`rounded-full border px-2 py-0.5 text-[10px] ${
                  seat.is_speaking_now
                    ? "border-[var(--accent)] bg-[var(--accent)]/15 text-[var(--accent)]"
                    : "border-[var(--line)] bg-[var(--bg-canvas)] text-[var(--text-primary)]"
                }`}
                title={seat.agent_role}
              >
                {seat.agent_display_name ?? seat.agent_role}
                {seat.is_speaking_now && <span className="ml-1">●</span>}
              </span>
            ))}
          </div>
          {meeting.latest_turn && (
            <div className="mt-2 rounded border border-[var(--line)] bg-[var(--bg-canvas)] p-2.5">
              <div className="text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
                Latest turn ·{" "}
                <span className="font-mono text-[var(--text-primary)]">
                  {meeting.latest_turn.agent_display_name ?? meeting.latest_turn.agent_role}
                </span>{" "}
                · {meeting.latest_turn.role_in_conversation}
              </div>
              <p className="mt-1 text-xs leading-snug text-[var(--text-primary)]">
                {meeting.latest_turn.content_preview}
              </p>
            </div>
          )}
        </div>
      </div>
    </Link>
  );
}
