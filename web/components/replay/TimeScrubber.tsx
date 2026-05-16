"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { format } from "date-fns";

/**
 * Bottom-of-page time scrubber for /replay.
 *
 * Bound to `?at=<iso>` in the URL. Owns its own play/pause state and an
 * auto-advance loop that walks `at` forward at a configurable multiplier
 * of wall-clock time. Reaching `latest` pauses automatically.
 */
export function TimeScrubber({
  earliest,
  latest,
  current,
}: {
  earliest: string;
  latest: string;
  current: string;
}) {
  const router = useRouter();

  const minMs = useMemo(() => new Date(earliest).getTime(), [earliest]);
  const maxMs = useMemo(() => new Date(latest).getTime(), [latest]);
  const currMs = useMemo(() => new Date(current).getTime(), [current]);

  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState<1 | 4 | 16 | 64>(16);
  const rafRef = useRef<number | null>(null);
  const lastTickRef = useRef<number | null>(null);

  // Auto-advance loop. Each animation frame, push the current `at` forward
  // by `(wallclock_delta_ms × speed)`. Stops when we hit `latest`.
  useEffect(() => {
    if (!playing) {
      lastTickRef.current = null;
      return;
    }
    function step(now: number) {
      if (lastTickRef.current == null) {
        lastTickRef.current = now;
      }
      const dt = now - lastTickRef.current;
      lastTickRef.current = now;
      const nextMs = Math.min(maxMs, currMs + dt * speed);
      if (nextMs >= maxMs) {
        setPlaying(false);
        updateAt(maxMs);
        return;
      }
      updateAt(nextMs);
      rafRef.current = requestAnimationFrame(step);
    }
    rafRef.current = requestAnimationFrame(step);
    return () => {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    };
    // We intentionally do not depend on `currMs` — including it would
    // restart the RAF every frame. `playing`/`speed` are the gates.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing, speed, maxMs]);

  function updateAt(ms: number) {
    const iso = new Date(ms).toISOString();
    router.replace(`/replay?at=${encodeURIComponent(iso)}`, { scroll: false });
  }

  const total = Math.max(1, maxMs - minMs);
  const pct = ((currMs - minMs) / total) * 100;

  return (
    <div className="sticky bottom-0 z-10 flex flex-col gap-2 border-t border-[var(--line)] bg-[var(--bg-surface)]/95 px-6 py-3 backdrop-blur">
      <div className="flex items-center gap-4 text-xs text-[var(--text-muted)]">
        <button
          type="button"
          onClick={() => setPlaying((v) => !v)}
          className="flex h-7 w-7 items-center justify-center rounded-full border border-[var(--line)] bg-[var(--bg-elevated)] text-sm hover:border-[var(--accent)]/40 hover:text-[var(--text-primary)]"
          aria-label={playing ? "Pause" : "Play"}
        >
          {playing ? "⏸" : "▶"}
        </button>
        <span className="font-mono text-[11px] tabular-nums text-[var(--text-primary)]">
          {format(new Date(currMs), "yyyy-MM-dd HH:mm:ss")}
        </span>
        <div className="ml-auto flex items-center gap-2">
          <span>speed</span>
          {([1, 4, 16, 64] as const).map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => setSpeed(s)}
              className={`rounded px-1.5 py-0.5 font-mono text-[10px] ${
                s === speed
                  ? "bg-[var(--accent)]/20 text-[var(--accent)]"
                  : "text-[var(--text-muted)] hover:text-[var(--text-primary)]"
              }`}
            >
              {s}×
            </button>
          ))}
        </div>
        <Link
          href="/"
          className="rounded border border-[var(--line)] px-2 py-0.5 text-[10px] hover:border-[var(--accent)]/40 hover:text-[var(--text-primary)]"
        >
          back to live
        </Link>
      </div>

      <div className="relative">
        <input
          type="range"
          min={minMs}
          max={maxMs}
          value={currMs}
          step={Math.max(1, Math.floor(total / 1000))}
          onChange={(e) => {
            setPlaying(false);
            updateAt(Number(e.target.value));
          }}
          className="slider w-full"
        />
        <div
          className="pointer-events-none absolute inset-y-0 left-0 rounded-full bg-[var(--accent)]/30"
          style={{ width: `${pct}%`, height: 4, top: "50%", transform: "translateY(-50%)" }}
        />
      </div>

      <div className="flex items-center justify-between font-mono text-[10px] text-[var(--text-muted)]">
        <span>{format(new Date(minMs), "MMM d, HH:mm")}</span>
        <span>{format(new Date(maxMs), "MMM d, HH:mm")}</span>
      </div>
    </div>
  );
}
