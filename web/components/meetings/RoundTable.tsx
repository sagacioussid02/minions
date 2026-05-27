"use client";

import type { Seat } from "@/lib/schemas";
import { seatCoords } from "@/lib/meetings/rituals";

/**
 * Top-down round-table visualization for living-org-spaces Surface A.
 *
 * Pure SVG/CSS — no canvas, no animation library. Each seat lives at a fixed
 * compass position relative to an ellipse center, so the same ritual always
 * looks the same (proposal Q1 resolution: fixed mapping per crew kind).
 *
 * The seat whose `is_speaking_now` is true gets a pulsing halo. The
 * animation is pure CSS — see <style jsx> at the bottom.
 *
 * Solo-agent crews (multi_agent=false) render one centered seat with a
 * spotlight ring instead of a round-table, per Q2 resolution.
 */
export function RoundTable({
  seats,
  multiAgent,
  size = "lg",
}: {
  seats: Seat[];
  multiAgent: boolean;
  size?: "lg" | "sm";
}) {
  const dims = size === "lg" ? LARGE_DIMS : SMALL_DIMS;
  const { width, height, rx, ry, seatR, fontSize } = dims;
  const cx = width / 2;
  const cy = height / 2;

  return (
    <div className="round-table-wrapper">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="block h-auto w-full"
        role="img"
        aria-label={multiAgent ? "Round-table meeting" : "Focused work card"}
      >
        {/* Table surface (the ellipse). Solo cards skip the ellipse — it's
            a single agent doing focused work, not a meeting. */}
        {multiAgent && (
          <ellipse
            cx={cx}
            cy={cy}
            rx={rx}
            ry={ry}
            fill="var(--bg-elevated)"
            stroke="var(--line)"
            strokeWidth={size === "lg" ? 1.5 : 1}
            strokeDasharray="4 4"
          />
        )}

        {seats.map((seat) => {
          const { x: dx, y: dy } = seatCoords(seat.seat_position, rx, ry);
          const x = cx + dx;
          const y = cy + dy;
          return (
            <SeatNode
              key={seat.agent_role}
              x={x}
              y={y}
              r={seatR}
              fontSize={fontSize}
              seat={seat}
              compact={size === "sm"}
            />
          );
        })}
      </svg>

      <style jsx>{`
        .round-table-wrapper :global(.speaker-halo) {
          animation: speaker-pulse 1.6s ease-in-out infinite;
          transform-origin: center;
          transform-box: fill-box;
        }
        @keyframes speaker-pulse {
          0%,
          100% {
            opacity: 0.45;
            transform: scale(1);
          }
          50% {
            opacity: 0.15;
            transform: scale(1.3);
          }
        }
      `}</style>
    </div>
  );
}

function SeatNode({
  x,
  y,
  r,
  fontSize,
  seat,
  compact,
}: {
  x: number;
  y: number;
  r: number;
  fontSize: number;
  seat: Seat;
  compact: boolean;
}) {
  const speaking = seat.is_speaking_now;
  const display = seat.agent_display_name ?? seat.agent_role;
  const labelY = y + r + (compact ? 12 : 16);
  const subY = labelY + (compact ? 10 : 14);
  return (
    <g>
      {/* Pulsing halo for the active speaker — drawn underneath the seat */}
      {speaking && (
        <circle
          cx={x}
          cy={y}
          r={r * 1.6}
          fill="var(--accent)"
          className="speaker-halo"
        />
      )}
      {/* Seat circle */}
      <circle
        cx={x}
        cy={y}
        r={r}
        fill={speaking ? "var(--accent)" : "var(--bg-surface)"}
        stroke={speaking ? "var(--accent)" : "var(--line)"}
        strokeWidth={1.5}
      />
      {/* Initial inside the seat */}
      <text
        x={x}
        y={y}
        textAnchor="middle"
        dominantBaseline="central"
        fill={speaking ? "var(--bg-elevated)" : "var(--text-primary)"}
        fontWeight={speaking ? 600 : 500}
        fontSize={fontSize * 0.9}
      >
        {initialFor(display)}
      </text>
      {/* Display name under the seat — skipped for small variant to save room */}
      {!compact && (
        <>
          <text
            x={x}
            y={labelY}
            textAnchor="middle"
            fill="var(--text-primary)"
            fontWeight={500}
            fontSize={fontSize}
          >
            {truncate(display, 14)}
          </text>
          <text
            x={x}
            y={subY}
            textAnchor="middle"
            fill="var(--text-muted)"
            fontSize={fontSize * 0.78}
          >
            {truncate(prettyRole(seat.agent_role), 16)}
          </text>
        </>
      )}
    </g>
  );
}

function initialFor(display: string): string {
  const cleaned = display.replace(/[_@#].*$/, "").trim();
  if (!cleaned) return "?";
  const parts = cleaned.split(/[\s-]+/).filter(Boolean);
  if (parts.length === 0) return cleaned[0]?.toUpperCase() ?? "?";
  if (parts.length === 1) return parts[0][0]?.toUpperCase() ?? "?";
  return (parts[0][0] + parts[1][0]).toUpperCase();
}

function prettyRole(role: string): string {
  return role
    .split("_")
    .map((p) => (p.length > 0 ? p[0].toUpperCase() + p.slice(1) : p))
    .join(" ");
}

function truncate(s: string, max: number): string {
  return s.length > max ? s.slice(0, max - 1) + "…" : s;
}

const LARGE_DIMS = {
  width: 560,
  height: 420,
  rx: 200,
  ry: 145,
  seatR: 30,
  fontSize: 13,
};

const SMALL_DIMS = {
  width: 220,
  height: 150,
  rx: 75,
  ry: 50,
  seatR: 9,
  fontSize: 9,
};
