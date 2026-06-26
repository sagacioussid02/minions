"use client";

import { useMemo, useState } from "react";
import type { MeetingDetail, MeetingTurn } from "@/lib/schemas";
import { humanize } from "@/lib/meetings/format";
import { Prose } from "@/lib/meetings/prose";
import { agentLabel } from "@/lib/roles";
import {
  useMeetingFeed,
  type ReplayControls as ReplayControlsType,
} from "@/lib/meetings/use-meeting-feed";
import { RoundTable } from "./RoundTable";

/**
 * 2D meeting room. The feed (SSE for live runs, controlled replay timer
 * for past runs) lives in `useMeetingFeed`; this component is now purely
 * the 2D draw target — round-table + transcript + replay controls.
 *
 * The 3D renderer (`Meeting3D`) consumes the exact same hook, so a replay
 * turn lands identically in both views: the speaker's bubble pops, the
 * halo flips to them, the transcript fades in at the top.
 */
export function LiveMeeting({
  initial,
  threeDHref,
}: {
  initial: MeetingDetail;
  threeDHref?: string;
}) {
  const { meeting, isLive, lastRevealedSequence, transportLabel, lastHeartbeatAt, transportError, replay } =
    useMeetingFeed(initial);
  const totalTurns = initial.turns.length;

  const turnsNewestFirst = useMemo(
    () => [...meeting.turns].reverse(),
    [meeting.turns],
  );

  return (
    <div className="mx-auto max-w-7xl space-y-4">
      <MeetingHeader
        meeting={meeting}
        baseMeeting={initial}
        transportLabel={transportLabel}
        lastHeartbeatAt={lastHeartbeatAt}
        threeDHref={threeDHref}
      />

      {!isLive && totalTurns > 0 && <ReplayControls replay={replay} />}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1.4fr_1fr]">
        <section className="rounded-xl border border-[var(--line)] bg-[var(--bg-surface)] p-4 lg:p-6">
          <RoundTable
            seats={meeting.seats}
            multiAgent={meeting.multi_agent}
            size="lg"
          />
        </section>

        <aside className="rounded-xl border border-[var(--line)] bg-[var(--bg-surface)]">
          <div className="flex items-center justify-between border-b border-[var(--line)] px-4 py-2">
            <div className="text-[10px] font-semibold uppercase tracking-wider text-[var(--text-muted)]">
              Transcript ({meeting.turns.length}
              {!isLive && totalTurns !== meeting.turns.length
                ? ` of ${totalTurns}`
                : ""}
              )
            </div>
            {isLive && (
              <div className="flex items-center gap-1.5 text-[10px] text-[var(--text-muted)]">
                <span
                  className={`inline-block h-1.5 w-1.5 rounded-full ${
                    transportError
                      ? "bg-[var(--state-warn)]"
                      : "bg-[var(--state-success)]"
                  }`}
                />
                {transportError ? "reconnecting" : "streaming"}
              </div>
            )}
          </div>
          <ol className="max-h-[600px] space-y-2 overflow-y-auto p-3">
            {turnsNewestFirst.length === 0 ? (
              <li className="rounded-lg border border-dashed border-[var(--line)] p-4 text-center text-xs text-[var(--text-muted)]">
                {isLive
                  ? "Waiting for the first turn…"
                  : "Press play to replay the meeting."}
              </li>
            ) : (
              turnsNewestFirst.map((turn) => (
                <TranscriptItem
                  key={turn.sequence}
                  turn={turn}
                  isNew={turn.sequence === lastRevealedSequence}
                />
              ))
            )}
          </ol>
        </aside>
      </div>

      <style jsx>{`
        :global(.transcript-new) {
          animation: turn-in 0.35s ease-out;
          transform-origin: top center;
        }
        @keyframes turn-in {
          0% {
            opacity: 0;
            transform: translateY(-6px) scale(0.98);
          }
          100% {
            opacity: 1;
            transform: translateY(0) scale(1);
          }
        }
      `}</style>
    </div>
  );
}

// ---------- Replay UI ----------

function ReplayControls({ replay }: { replay: ReplayControlsType }) {
  const { revealed, total, playing, speed, onPlayPause, onRestart, onSkipToEnd, onSpeed } =
    replay;
  const atEnd = revealed >= total;
  return (
    <div className="flex flex-wrap items-center gap-3 rounded-xl border border-[var(--accent)]/30 bg-[var(--accent)]/5 px-3 py-2">
      <div className="text-[10px] font-semibold uppercase tracking-wider text-[var(--accent)]">
        Replay
      </div>
      <div className="flex items-center gap-1.5">
        <button
          type="button"
          onClick={onRestart}
          disabled={revealed === 0}
          title="Restart from the first turn"
          className="rounded border border-[var(--line)] bg-[var(--bg-elevated)] px-2 py-0.5 text-xs text-[var(--text-primary)] disabled:cursor-not-allowed disabled:opacity-40 hover:border-[var(--accent)]/60"
        >
          ⏮
        </button>
        <button
          type="button"
          onClick={onPlayPause}
          disabled={atEnd}
          title={playing ? "Pause replay" : "Play replay"}
          className="rounded border border-[var(--accent)] bg-[var(--accent)] px-2.5 py-0.5 text-xs font-medium text-white disabled:cursor-not-allowed disabled:opacity-40"
        >
          {playing ? "⏸ Pause" : atEnd ? "Done" : "▶ Play"}
        </button>
        <button
          type="button"
          onClick={onSkipToEnd}
          disabled={atEnd}
          title="Skip to the end of the meeting"
          className="rounded border border-[var(--line)] bg-[var(--bg-elevated)] px-2 py-0.5 text-xs text-[var(--text-primary)] disabled:cursor-not-allowed disabled:opacity-40 hover:border-[var(--accent)]/60"
        >
          ⏭
        </button>
      </div>
      <div className="flex items-center gap-1 text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
        Speed:
        {([0.5, 1, 2, 4] as const).map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => onSpeed(s)}
            className={`rounded px-1.5 py-0.5 font-mono ${
              s === speed
                ? "bg-[var(--accent)] text-white"
                : "bg-[var(--bg-elevated)] text-[var(--text-primary)] hover:bg-[var(--bg-canvas)]"
            }`}
          >
            {s}×
          </button>
        ))}
      </div>
      <div className="ml-auto font-mono text-[10px] text-[var(--text-muted)]">
        {revealed} / {total}
      </div>
    </div>
  );
}

// ---------- Transcript item ----------

function TranscriptItem({ turn, isNew }: { turn: MeetingTurn; isNew: boolean }) {
  const { preview, body, isJson } = humanize(turn.content_full);
  const [expanded, setExpanded] = useState(false);
  const canExpand = isJson || body !== preview;
  return (
    <li
      className={`rounded-lg border border-[var(--line)] bg-[var(--bg-canvas)] p-2.5 ${
        isNew ? "transcript-new border-[var(--accent)]/40" : ""
      }`}
    >
      <div className="flex flex-wrap items-center gap-1.5 text-[10px] text-[var(--text-muted)]">
        <span className="font-medium text-[var(--text-primary)]">
          {agentLabel(turn.agent_display_name, turn.agent_role)}
        </span>
        <span>·</span>
        <span className="uppercase tracking-wider">{turn.role_in_conversation}</span>
        <span>·</span>
        <span>#{turn.sequence}</span>
        {isJson && (
          <>
            <span>·</span>
            <span className="rounded bg-[var(--accent)]/10 px-1 text-[var(--accent)]">
              structured
            </span>
          </>
        )}
      </div>
      {expanded ? (
        isJson ? (
          <pre className="mt-1.5 max-h-80 overflow-auto whitespace-pre-wrap rounded bg-[var(--bg-elevated)] p-2 font-mono text-[10px] leading-snug text-[var(--text-primary)]">
            {body}
          </pre>
        ) : (
          <div className="mt-1.5 max-h-80 overflow-auto rounded bg-[var(--bg-elevated)] p-2.5">
            <Prose text={body} />
          </div>
        )
      ) : (
        <p className="mt-1.5 text-[11.5px] leading-relaxed text-[var(--text-primary)]">
          {preview}
        </p>
      )}
      {canExpand && (
        <button
          type="button"
          onClick={() => setExpanded((x) => !x)}
          className="mt-1 text-[10px] text-[var(--accent)] hover:underline"
        >
          {expanded ? "collapse" : isJson ? "show raw JSON" : "show full"}
        </button>
      )}
    </li>
  );
}

// ---------- Header ----------

function MeetingHeader({
  meeting,
  baseMeeting,
  transportLabel,
  lastHeartbeatAt,
  threeDHref,
}: {
  meeting: MeetingDetail;
  baseMeeting: MeetingDetail;
  transportLabel: string | null;
  lastHeartbeatAt: number;
  threeDHref?: string;
}) {
  const statusClass =
    meeting.status === "in_progress"
      ? "bg-[var(--state-success)]/15 text-[var(--state-success)]"
      : meeting.status === "failed"
        ? "bg-[var(--state-danger)]/15 text-[var(--state-danger)]"
        : "bg-[var(--bg-canvas)] text-[var(--text-muted)]";
  return (
    <header>
      <div className="flex items-center gap-2">
        <h1 className="text-lg font-semibold text-[var(--text-primary)]">
          {meeting.ritual_label}
        </h1>
        {meeting.project && (
          <span className="rounded bg-[var(--bg-canvas)] px-1.5 py-0.5 font-mono text-[11px] text-[var(--text-muted)]">
            {meeting.project}
          </span>
        )}
        <span className={`rounded px-1.5 py-0.5 text-[10px] uppercase tracking-wider ${statusClass}`}>
          {meeting.status === "in_progress" ? "live" : meeting.status}
        </span>
        {transportLabel && (
          <span className="text-[10px] text-[var(--text-muted)]">· {transportLabel}</span>
        )}
        {threeDHref && (
          <a
            href={threeDHref}
            className="ml-auto rounded border border-[var(--accent)]/50 bg-[var(--accent)]/10 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider text-[var(--accent)] hover:bg-[var(--accent)]/20"
            title="Open the 3D round-table view"
          >
            3D view →
          </a>
        )}
      </div>
      <p className="mt-1 text-sm text-[var(--text-muted)]">{meeting.ritual_agenda}</p>
      <div className="mt-2 flex flex-wrap gap-4 font-mono text-[10px] text-[var(--text-muted)]">
        <span>run · {meeting.run_id.slice(0, 12)}</span>
        <span>started · {new Date(baseMeeting.started_at).toLocaleString()}</span>
        <span>turns · {baseMeeting.total_turns}</span>
        {lastHeartbeatAt > 0 && meeting.status === "in_progress" && (
          <span title="Last SSE heartbeat received from server">
            {/* Display-only relative time: wall-clock is read at render and
                naturally refreshes on each SSE-driven re-render of this live
                component. Not used for any logic, so the render-time read is
                intentional. */}
            {/* eslint-disable-next-line react-hooks/purity */}
            last beat · {Math.max(0, Math.round((Date.now() - lastHeartbeatAt) / 1000))}s ago
          </span>
        )}
      </div>
    </header>
  );
}
