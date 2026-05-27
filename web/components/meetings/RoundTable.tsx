"use client";

import type { Seat } from "@/lib/schemas";
import { seatCoords, type SeatPosition } from "@/lib/meetings/rituals";

/**
 * Top-down round-table visualization for living-org-spaces Surface A.
 *
 * SVG for the table + seats, HTML overlay for the per-seat chat bubbles
 * (HTML is much friendlier for variable-length text wrapping than
 * <foreignObject>). Both layers share the same viewBox aspect ratio
 * via the wrapper's intrinsic ratio.
 *
 * Each seat lives at a fixed compass position from MEETING_RITUALS so
 * the same ritual always looks the same. The seat whose
 * is_speaking_now=true gets a pulsing accent halo; every seat with a
 * last_turn_preview gets a chat bubble floating radially outside the
 * ellipse showing what that agent most recently said.
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
  const { width, height, rx, ry, seatR, fontSize, bubbleOffset, bubbleMaxWidth } = dims;
  const cx = width / 2;
  const cy = height / 2;

  return (
    <div
      className="round-table-wrapper relative w-full"
      style={{ aspectRatio: `${width} / ${height}` }}
    >
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="absolute inset-0 block h-full w-full"
        role="img"
        aria-label={multiAgent ? "Round-table meeting" : "Focused work card"}
      >
        {/* Table surface (the ellipse). Solo cards skip it. */}
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
          return (
            <SeatNode
              key={seat.agent_role}
              x={cx + dx}
              y={cy + dy}
              r={seatR}
              fontSize={fontSize}
              seat={seat}
              compact={size === "sm"}
            />
          );
        })}
      </svg>

      {/* HTML chat-bubble overlay — only rendered on the large variant
          where there's enough room for readable text. */}
      {size === "lg" &&
        seats.map((seat) => {
          if (!seat.last_turn_preview) return null;
          const { x: dx, y: dy } = seatCoords(seat.seat_position, rx, ry);
          const seatXPct = ((cx + dx) / width) * 100;
          const seatYPct = ((cy + dy) / height) * 100;
          return (
            <ChatBubble
              key={`${seat.agent_role}-${seat.last_turn_sequence ?? 0}`}
              seat={seat}
              seatXPct={seatXPct}
              seatYPct={seatYPct}
              bubbleOffset={bubbleOffset}
              maxWidth={bubbleMaxWidth}
              containerWidth={width}
              containerHeight={height}
            />
          );
        })}

      <style jsx>{`
        .round-table-wrapper :global(.speaker-halo) {
          animation: speaker-pulse 1.6s ease-in-out infinite;
          transform-origin: center;
          transform-box: fill-box;
        }
        .round-table-wrapper :global(.chat-bubble) {
          animation: bubble-pop 0.35s cubic-bezier(0.34, 1.56, 0.64, 1);
          transform-origin: center bottom;
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
        @keyframes bubble-pop {
          0% {
            opacity: 0;
            transform: translateY(4px) scale(0.92);
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
      {speaking && (
        <circle
          cx={x}
          cy={y}
          r={r * 1.6}
          fill="var(--accent)"
          className="speaker-halo"
        />
      )}
      <circle
        cx={x}
        cy={y}
        r={r}
        fill={speaking ? "var(--accent)" : "var(--bg-surface)"}
        stroke={speaking ? "var(--accent)" : "var(--line)"}
        strokeWidth={1.5}
      />
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

/**
 * Chat bubble overlay positioned radially outward from each seat.
 *
 * Re-keyed per (agent_role, last_turn_sequence) by the parent so React
 * tears down + remounts the element when this seat speaks a new turn,
 * which retriggers the bubble-pop CSS animation.
 */
function ChatBubble({
  seat,
  seatXPct,
  seatYPct,
  bubbleOffset,
  maxWidth,
  containerWidth,
  containerHeight,
}: {
  seat: Seat;
  seatXPct: number;
  seatYPct: number;
  bubbleOffset: number;
  maxWidth: number;
  containerWidth: number;
  containerHeight: number;
}) {
  if (!seat.last_turn_preview) return null;
  const placement = bubblePlacement(seat.seat_position);
  // Convert pixel-offset to % of container so the bubble scales with the SVG.
  const dxPct = (placement.dx * bubbleOffset) / containerWidth;
  const dyPct = (placement.dy * bubbleOffset) / containerHeight;

  // Position the bubble's anchor edge (pointer) at the seat's outward edge.
  const style: React.CSSProperties = {
    left: `calc(${seatXPct}% + ${dxPct * 100}%)`,
    top: `calc(${seatYPct}% + ${dyPct * 100}%)`,
    maxWidth: `${maxWidth}px`,
    transform: placement.transform,
  };

  return (
    <div
      className={`chat-bubble pointer-events-none absolute z-10 ${
        seat.is_speaking_now ? "ring-1 ring-[var(--accent)]/60" : ""
      } rounded-lg border border-[var(--line)] bg-[var(--bg-elevated)] px-2.5 py-1.5 shadow-sm`}
      style={style}
    >
      <div className="text-[10px] font-medium leading-tight text-[var(--text-primary)]">
        {clamp(seat.last_turn_preview, 140)}
      </div>
      <BubblePointer placement={placement.pointer} />
    </div>
  );
}

function BubblePointer({ placement }: { placement: "down" | "up" | "left" | "right" }) {
  // A small triangle pointing from the bubble back toward the seat.
  // Sized 8x8 and positioned at the bubble edge.
  const common = "absolute h-0 w-0";
  const triangleClasses: Record<typeof placement, string> = {
    down:
      "left-1/2 -translate-x-1/2 -bottom-[6px] border-x-[6px] border-x-transparent border-t-[6px] border-t-[var(--line)] after:absolute after:left-[-5px] after:top-[-6px] after:h-0 after:w-0 after:border-x-[5px] after:border-x-transparent after:border-t-[5px] after:border-t-[var(--bg-elevated)]",
    up:
      "left-1/2 -translate-x-1/2 -top-[6px] border-x-[6px] border-x-transparent border-b-[6px] border-b-[var(--line)] after:absolute after:left-[-5px] after:top-[1px] after:h-0 after:w-0 after:border-x-[5px] after:border-x-transparent after:border-b-[5px] after:border-b-[var(--bg-elevated)]",
    left:
      "top-1/2 -translate-y-1/2 -left-[6px] border-y-[6px] border-y-transparent border-r-[6px] border-r-[var(--line)] after:absolute after:top-[-5px] after:left-[1px] after:h-0 after:w-0 after:border-y-[5px] after:border-y-transparent after:border-r-[5px] after:border-r-[var(--bg-elevated)]",
    right:
      "top-1/2 -translate-y-1/2 -right-[6px] border-y-[6px] border-y-transparent border-l-[6px] border-l-[var(--line)] after:absolute after:top-[-5px] after:left-[-6px] after:h-0 after:w-0 after:border-y-[5px] after:border-y-transparent after:border-l-[5px] after:border-l-[var(--bg-elevated)]",
  };
  return <span className={`${common} ${triangleClasses[placement]}`} />;
}

interface BubblePlacement {
  // Direction the bubble offsets from the seat (radial outward).
  dx: number; // unit vector
  dy: number;
  // CSS transform to anchor the bubble's pointer edge over the seat.
  transform: string;
  // Which side of the bubble the pointer triangle sits on.
  pointer: "up" | "down" | "left" | "right";
}

function bubblePlacement(position: SeatPosition): BubblePlacement {
  // For each compass seat, place the bubble radially outward from the
  // ellipse center. Pointer points BACK toward the seat. translate(-50%, X)
  // centers the bubble on the radial axis.
  switch (position) {
    case "north":
      return { dx: 0, dy: -1, transform: "translate(-50%, -100%)", pointer: "down" };
    case "northeast":
      return { dx: 0.7, dy: -0.7, transform: "translate(0%, -100%)", pointer: "down" };
    case "east":
      return { dx: 1, dy: 0, transform: "translate(0%, -50%)", pointer: "left" };
    case "southeast":
      return { dx: 0.7, dy: 0.7, transform: "translate(0%, 0%)", pointer: "up" };
    case "south":
      return { dx: 0, dy: 1, transform: "translate(-50%, 0%)", pointer: "up" };
    case "southwest":
      return { dx: -0.7, dy: 0.7, transform: "translate(-100%, 0%)", pointer: "up" };
    case "west":
      return { dx: -1, dy: 0, transform: "translate(-100%, -50%)", pointer: "right" };
    case "northwest":
      return { dx: -0.7, dy: -0.7, transform: "translate(-100%, -100%)", pointer: "down" };
    case "center":
      return { dx: 0, dy: -1, transform: "translate(-50%, -100%)", pointer: "down" };
  }
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

function clamp(s: string, max: number): string {
  const trimmed = s.trim().replace(/\s+/g, " ");
  return trimmed.length > max ? trimmed.slice(0, max - 1) + "…" : trimmed;
}

const LARGE_DIMS = {
  width: 720,
  height: 480,
  rx: 230,
  ry: 160,
  seatR: 32,
  fontSize: 13,
  bubbleOffset: 60, // pixel offset from seat center
  bubbleMaxWidth: 200,
};

const SMALL_DIMS = {
  width: 220,
  height: 150,
  rx: 75,
  ry: 50,
  seatR: 9,
  fontSize: 9,
  bubbleOffset: 0,
  bubbleMaxWidth: 0,
};
