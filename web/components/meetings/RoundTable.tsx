"use client";

import { Avatar } from "@/components/Avatar";
import type { Seat } from "@/lib/schemas";
import { seatCoords, type SeatPosition } from "@/lib/meetings/rituals";
import { humanize } from "@/lib/meetings/format";
import { roleShortLabel } from "@/lib/roles";

/**
 * Top-down round-table visualization for living-org-spaces Surface A.
 *
 * Layout strategy: a thin SVG layer draws only the ellipse (the "table
 * surface"); every seat + chat bubble lives in an HTML overlay
 * positioned with percentage coords. This lets us use real dicebear
 * avatars per seat (via <Avatar>) instead of letter circles, and gives
 * chat bubbles the full HTML text-rendering toolbox.
 *
 * Same crew always looks the same — seat positions come from
 * MEETING_RITUALS[crew].seat_layout. The active speaker gets a CSS
 * pulse ring around their avatar; every seat with a last_turn_preview
 * gets a chat bubble radially outside the table.
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
  const { width, height, rx, ry, avatarSize, bubbleOffset, bubbleMaxWidth } = dims;
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
      </svg>

      {/* Seat layer */}
      {seats.map((seat) => {
        const { x: dx, y: dy } = seatCoords(seat.seat_position, rx, ry);
        const leftPct = ((cx + dx) / width) * 100;
        const topPct = ((cy + dy) / height) * 100;
        return (
          <SeatNode
            key={seat.agent_role}
            seat={seat}
            leftPct={leftPct}
            topPct={topPct}
            avatarSize={avatarSize}
            compact={size === "sm"}
          />
        );
      })}

      {/* Chat-bubble layer — only on the large variant */}
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
        .round-table-wrapper :global(.seat-speaking-ring) {
          animation: ring-pulse 1.6s ease-in-out infinite;
        }
        .round-table-wrapper :global(.chat-bubble) {
          animation: bubble-pop 0.4s cubic-bezier(0.34, 1.56, 0.64, 1);
        }
        @keyframes ring-pulse {
          0%,
          100% {
            box-shadow: 0 0 0 3px var(--accent), 0 0 0 6px rgb(14 165 233 / 0.18);
          }
          50% {
            box-shadow: 0 0 0 3px var(--accent), 0 0 0 14px rgb(14 165 233 / 0);
          }
        }
        @keyframes bubble-pop {
          0% {
            opacity: 0;
            transform: translateY(6px) scale(0.92);
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
  seat,
  leftPct,
  topPct,
  avatarSize,
  compact,
}: {
  seat: Seat;
  leftPct: number;
  topPct: number;
  avatarSize: number;
  compact: boolean;
}) {
  const hasName = Boolean(seat.agent_display_name?.trim());
  const display = seat.agent_display_name?.trim() || roleShortLabel(seat.agent_role);
  // Seed the avatar on the stable role id so it doesn't change when a
  // display name resolves later.
  const seed = seat.agent_role;
  const ringColor = seat.is_speaking_now ? "var(--accent)" : undefined;
  return (
    <div
      className="absolute flex flex-col items-center"
      style={{
        left: `${leftPct}%`,
        top: `${topPct}%`,
        transform: "translate(-50%, -50%)",
        width: avatarSize + 60,
      }}
    >
      <span
        className={seat.is_speaking_now ? "seat-speaking-ring rounded-full" : "rounded-full"}
        style={{
          width: avatarSize,
          height: avatarSize,
          // boxShadow when not animating — gives a subtle ring even at rest.
          boxShadow: seat.is_speaking_now ? undefined : "0 0 0 1.5px var(--line)",
          borderRadius: "50%",
        }}
      >
        <Avatar seed={seed} size={avatarSize} ring={ringColor} />
      </span>
      {!compact && (
        <>
          <div
            className="mt-1.5 text-center text-[11px] font-medium leading-tight text-[var(--text-primary)]"
            style={{ maxWidth: avatarSize + 50 }}
          >
            {truncate(display, 18)}
          </div>
          {hasName && (
            <div
              className="text-center text-[9px] uppercase tracking-wider text-[var(--text-muted)]"
              style={{ maxWidth: avatarSize + 50 }}
            >
              {truncate(roleShortLabel(seat.agent_role), 20)}
            </div>
          )}
        </>
      )}
    </div>
  );
}

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
  const dxPct = (placement.dx * bubbleOffset) / containerWidth;
  const dyPct = (placement.dy * bubbleOffset) / containerHeight;
  const { preview, isJson } = humanize(seat.last_turn_preview);
  if (!preview) return null;

  const style: React.CSSProperties = {
    left: `calc(${seatXPct}% + ${dxPct * 100}%)`,
    top: `calc(${seatYPct}% + ${dyPct * 100}%)`,
    maxWidth: `${maxWidth}px`,
    transform: placement.transform,
  };

  return (
    <div
      className={`chat-bubble pointer-events-none absolute z-10 rounded-lg border bg-[var(--bg-elevated)] px-2.5 py-1.5 shadow-md ${
        seat.is_speaking_now
          ? "border-[var(--accent)]"
          : "border-[var(--line)]"
      }`}
      style={style}
    >
      <div className="flex items-start gap-1.5">
        {isJson && (
          <span className="mt-[1px] text-[10px]" title="Structured plan output">
            📋
          </span>
        )}
        <div className="text-[11px] leading-snug text-[var(--text-primary)]">
          {preview}
        </div>
      </div>
      <BubblePointer placement={placement.pointer} />
    </div>
  );
}

function BubblePointer({ placement }: { placement: "down" | "up" | "left" | "right" }) {
  const classes: Record<typeof placement, string> = {
    down:
      "left-1/2 -translate-x-1/2 -bottom-[6px] border-x-[6px] border-x-transparent border-t-[6px] border-t-[var(--line)] after:absolute after:left-[-5px] after:top-[-6px] after:h-0 after:w-0 after:border-x-[5px] after:border-x-transparent after:border-t-[5px] after:border-t-[var(--bg-elevated)]",
    up:
      "left-1/2 -translate-x-1/2 -top-[6px] border-x-[6px] border-x-transparent border-b-[6px] border-b-[var(--line)] after:absolute after:left-[-5px] after:top-[1px] after:h-0 after:w-0 after:border-x-[5px] after:border-x-transparent after:border-b-[5px] after:border-b-[var(--bg-elevated)]",
    left:
      "top-1/2 -translate-y-1/2 -left-[6px] border-y-[6px] border-y-transparent border-r-[6px] border-r-[var(--line)] after:absolute after:top-[-5px] after:left-[1px] after:h-0 after:w-0 after:border-y-[5px] after:border-y-transparent after:border-r-[5px] after:border-r-[var(--bg-elevated)]",
    right:
      "top-1/2 -translate-y-1/2 -right-[6px] border-y-[6px] border-y-transparent border-l-[6px] border-l-[var(--line)] after:absolute after:top-[-5px] after:left-[-6px] after:h-0 after:w-0 after:border-y-[5px] after:border-y-transparent after:border-l-[5px] after:border-l-[var(--bg-elevated)]",
  };
  return <span className={`absolute h-0 w-0 ${classes[placement]}`} />;
}

interface BubblePlacement {
  dx: number;
  dy: number;
  transform: string;
  pointer: "up" | "down" | "left" | "right";
}

function bubblePlacement(position: SeatPosition): BubblePlacement {
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

function truncate(s: string, max: number): string {
  return s.length > max ? s.slice(0, max - 1) + "…" : s;
}

const LARGE_DIMS = {
  width: 720,
  height: 480,
  rx: 230,
  ry: 160,
  avatarSize: 56,
  bubbleOffset: 70,
  bubbleMaxWidth: 240,
};

const SMALL_DIMS = {
  width: 220,
  height: 150,
  rx: 75,
  ry: 50,
  avatarSize: 22,
  bubbleOffset: 0,
  bubbleMaxWidth: 0,
};
