import type { MeetingDetail as MeetingDetailType } from "@/lib/schemas";

/**
 * Bare-bones meeting detail — PR 1 of living-org-spaces Surface A.
 *
 * Renders the seat roster + every turn in conversation order. The SVG
 * round-table + scrubbable replay land in PR 2.
 */
export function MeetingDetail({ meeting }: { meeting: MeetingDetailType }) {
  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <header>
        <div className="flex items-center gap-2">
          <h1 className="text-lg font-semibold text-[var(--text-primary)]">
            {meeting.ritual_label}
          </h1>
          {meeting.project && (
            <span className="rounded bg-[var(--surface-muted)]/60 px-1.5 py-0.5 font-mono text-[11px] text-[var(--text-muted)]">
              {meeting.project}
            </span>
          )}
          <span
            className={`rounded px-1.5 py-0.5 text-[10px] uppercase tracking-wider ${
              meeting.status === "in_progress"
                ? "bg-[var(--state-success)]/15 text-[var(--state-success)]"
                : meeting.status === "failed"
                  ? "bg-[var(--state-danger)]/15 text-[var(--state-danger)]"
                  : "bg-[var(--surface-muted)]/60 text-[var(--text-muted)]"
            }`}
          >
            {meeting.status === "in_progress" ? "live" : meeting.status}
          </span>
        </div>
        <p className="mt-1 text-sm text-[var(--text-muted)]">{meeting.ritual_agenda}</p>
        <div className="mt-2 flex gap-4 font-mono text-[10px] text-[var(--text-muted)]">
          <span>run · {meeting.run_id.slice(0, 12)}</span>
          <span>started · {new Date(meeting.started_at).toLocaleString()}</span>
          <span>turns · {meeting.total_turns}</span>
        </div>
      </header>

      <section>
        <h2 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-[var(--text-muted)]">
          Seats ({meeting.seats.length})
        </h2>
        <div className="flex flex-wrap gap-2">
          {meeting.seats.map((seat) => (
            <div
              key={seat.agent_role}
              className={`rounded-lg border px-3 py-2 ${
                seat.is_speaking_now
                  ? "border-[var(--accent)] bg-[var(--accent)]/10"
                  : "border-[var(--line)] bg-[var(--surface-muted)]/40"
              }`}
            >
              <div className="text-sm font-medium text-[var(--text-primary)]">
                {seat.agent_display_name ?? seat.agent_role}
              </div>
              <div className="text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
                {seat.agent_role} · {seat.seat_position}
              </div>
            </div>
          ))}
        </div>
      </section>

      <section>
        <h2 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-[var(--text-muted)]">
          Transcript
        </h2>
        <ol className="space-y-3">
          {meeting.turns.map((turn) => (
            <li
              key={turn.sequence}
              className="rounded-lg border border-[var(--line)] bg-[var(--surface-muted)]/30 p-3"
            >
              <div className="flex items-center gap-2 text-[11px] text-[var(--text-muted)]">
                <span className="font-mono text-[var(--text-primary)]">
                  {turn.agent_display_name ?? turn.agent_role}
                </span>
                <span>·</span>
                <span className="uppercase tracking-wider">{turn.role_in_conversation}</span>
                <span>·</span>
                <span>#{turn.sequence}</span>
                <span>·</span>
                <span>{new Date(turn.created_at).toLocaleTimeString()}</span>
              </div>
              <p className="mt-2 whitespace-pre-wrap text-sm leading-relaxed text-[var(--text-primary)]">
                {turn.content_full}
              </p>
            </li>
          ))}
        </ol>
      </section>
    </div>
  );
}
