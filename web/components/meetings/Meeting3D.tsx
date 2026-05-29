"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import { Billboard, Html, OrbitControls } from "@react-three/drei";
import * as THREE from "three";
import type { MeetingDetail, MeetingTurn, Seat } from "@/lib/schemas";
import { useMeetingFeed, type ReplayControls } from "@/lib/meetings/use-meeting-feed";
import { seatCoords } from "@/lib/meetings/rituals";
import { humanize, cleanInlineText } from "@/lib/meetings/format";
import { agentLabel } from "@/lib/roles";
import { buildAvatarSvg } from "@/components/Avatar";

// Typewriter reveal speed for the active speaker's bubble (chars/sec) and
// how long to hold the finished bubble before it collapses.
const STREAM_CHARS_PER_SEC = 95;
const HOLD_AFTER_DONE_S = 1.4;
const MAX_STREAM_CHARS = 700;

/** Text the active speaker "says" — clean prose, or the headline for JSON. */
function speakTextFor(turn: MeetingTurn): string {
  const h = humanize(turn.content_full);
  if (h.isJson) return h.preview;
  return cleanInlineText(turn.content_full).slice(0, MAX_STREAM_CHARS);
}

/**
 * 3D round-table renderer. Second consumer of `useMeetingFeed` — the same
 * SSE/replay feed that drives the 2D `LiveMeeting`. Only the draw target
 * differs, so live + replay turns land here with identical latency.
 *
 * Lazy-loaded (`ssr:false`) so three / R3F / drei never enter the shared
 * bundle; this module is imported only on the `/meetings/[run_id]/3d` route.
 */

const TABLE_RX = 4.2;
const TABLE_RZ = 2.7;
const AVATAR_Y = 1.35;

export default function Meeting3D({
  initial,
  backHref,
  reducedMotion = false,
}: {
  initial: MeetingDetail;
  backHref: string;
  reducedMotion?: boolean;
}) {
  const { meeting, isLive, lastRevealedSequence, transportLabel, replay } =
    useMeetingFeed(initial);

  const controlsRef = useRef<React.ComponentRef<typeof OrbitControls> | null>(null);
  const [frameloop, setFrameloop] = useState<"always" | "never">("always");

  // Pause the render loop while the tab is hidden.
  useEffect(() => {
    const onVis = () => setFrameloop(document.hidden ? "never" : "always");
    document.addEventListener("visibilitychange", onVis);
    onVis();
    return () => document.removeEventListener("visibilitychange", onVis);
  }, []);

  const emphasizedRole = useMemo(() => {
    const speaking = meeting.seats.find(
      (s) => s.last_turn_sequence != null && s.last_turn_sequence === lastRevealedSequence,
    );
    return speaking?.agent_role ?? meeting.seats.find((s) => s.is_speaking_now)?.agent_role ?? null;
  }, [meeting.seats, lastRevealedSequence]);

  const speakText = useMemo(() => {
    const turn = meeting.turns.find((t) => t.sequence === lastRevealedSequence);
    return turn ? speakTextFor(turn) : "";
  }, [meeting.turns, lastRevealedSequence]);

  const hasSeats = meeting.seats.length > 0;

  return (
    <div className="relative flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2 border-b border-[var(--line)] px-1 pb-2">
        <h1 className="text-base font-semibold text-[var(--text-primary)]">
          {meeting.ritual_label}
        </h1>
        {meeting.project && (
          <span className="rounded bg-[var(--bg-canvas)] px-1.5 py-0.5 font-mono text-[11px] text-[var(--text-muted)]">
            {meeting.project}
          </span>
        )}
        <span className="rounded bg-[var(--bg-canvas)] px-1.5 py-0.5 text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
          {meeting.status === "in_progress" ? "live" : meeting.status}
        </span>
        {transportLabel && (
          <span className="text-[10px] text-[var(--text-muted)]">· {transportLabel}</span>
        )}
        <div className="ml-auto flex items-center gap-2">
          <button
            type="button"
            onClick={() => controlsRef.current?.reset?.()}
            className="rounded border border-[var(--line)] bg-[var(--bg-elevated)] px-2 py-0.5 text-[10px] text-[var(--text-primary)] hover:border-[var(--accent)]/60"
          >
            reset view
          </button>
          <a
            href={backHref}
            className="rounded border border-[var(--line)] bg-[var(--bg-elevated)] px-2 py-0.5 text-[10px] text-[var(--text-primary)] hover:border-[var(--accent)]/60"
          >
            ← back to 2D
          </a>
        </div>
      </div>

      {!isLive && initial.turns.length > 0 && (
        <div className="px-1 py-2">
          <Replay3DControls replay={replay} />
        </div>
      )}

      <div className="relative h-[72vh] min-h-[420px] w-full overflow-hidden rounded-xl border border-[var(--line)] bg-[var(--bg-canvas)]">
        {!hasSeats && (
          <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center">
            <span className="rounded-lg border border-dashed border-[var(--line)] bg-[var(--bg-surface)]/80 px-4 py-2 text-xs text-[var(--text-muted)]">
              {isLive ? "Waiting for the first turn…" : "Press play to replay the meeting."}
            </span>
          </div>
        )}
        <Canvas
          frameloop={frameloop}
          dpr={[1, 2]}
          camera={{ position: [0, 6.5, 13], fov: 47 }}
          gl={{ antialias: true, powerPreference: "high-performance" }}
        >
          <color attach="background" args={["#eef2f8"]} />
          <fog attach="fog" args={["#eef2f8", 22, 40]} />
          <hemisphereLight args={["#ffffff", "#cdd6e4", 1.05]} />
          <ambientLight intensity={0.5} />
          <directionalLight position={[6, 10, 6]} intensity={0.9} castShadow />
          <directionalLight position={[-6, 6, -4]} intensity={0.35} />

          <Table />
          <TranscriptScreen turns={meeting.turns} lastSeq={lastRevealedSequence} />

          {meeting.seats.map((seat) => {
            const emphasized = seat.agent_role === emphasizedRole;
            return (
              <SeatNode
                key={seat.agent_role}
                seat={seat}
                emphasized={emphasized}
                reducedMotion={reducedMotion}
                streamText={emphasized ? speakText : ""}
                streamSeq={lastRevealedSequence}
              />
            );
          })}

          <OrbitControls
            ref={controlsRef}
            enablePan={false}
            enableRotate={!reducedMotion}
            minDistance={6}
            maxDistance={22}
            minPolarAngle={0.2}
            maxPolarAngle={Math.PI / 2.15}
            target={[0, 2, -2]}
          />
        </Canvas>
      </div>
    </div>
  );
}

function Table() {
  return (
    <group>
      {/* wooden table body */}
      <mesh position={[0, -0.05, 0]} scale={[TABLE_RX, 1, TABLE_RZ]} receiveShadow>
        <cylinderGeometry args={[1, 1, 0.3, 64]} />
        <meshStandardMaterial color="#7a5230" metalness={0} roughness={0.85} />
      </mesh>
      {/* polished wood top with a lighter inlay */}
      <mesh position={[0, 0.105, 0]} scale={[TABLE_RX * 0.97, 1, TABLE_RZ * 0.97]} receiveShadow>
        <cylinderGeometry args={[1, 1, 0.02, 64]} />
        <meshStandardMaterial color="#a9743f" metalness={0.05} roughness={0.55} />
      </mesh>
      <mesh position={[0, 0.118, 0]} scale={[TABLE_RX * 0.6, 1, TABLE_RZ * 0.6]}>
        <cylinderGeometry args={[1, 1, 0.01, 64]} />
        <meshStandardMaterial color="#b9844c" metalness={0.05} roughness={0.5} />
      </mesh>
      {/* office floor */}
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.21, 0]} receiveShadow>
        <planeGeometry args={[60, 60]} />
        <meshStandardMaterial color="#dfe5ee" roughness={0.95} />
      </mesh>
    </group>
  );
}

/**
 * Big presentation screen at the head of the room that the table faces.
 * Shows the running transcript — the latest revealed turn is highlighted,
 * so the room visibly "watches" the conversation being written.
 */
const SCREEN_Z = -(TABLE_RZ + 4.1);
const SCREEN_W = 9.2;
const SCREEN_H = 5.3;
// Canvas backing the screen texture — same aspect as the panel for no stretch.
const CANVAS_W = 1024;
const CANVAS_H = Math.round((CANVAS_W * SCREEN_H) / SCREEN_W);

function TranscriptScreen({ turns, lastSeq }: { turns: MeetingTurn[]; lastSeq: number }) {
  const texture = useTranscriptTexture(turns, lastSeq);

  return (
    <group position={[0, 3.9, SCREEN_Z]}>
      {/* bezel */}
      <mesh position={[0, 0, -0.1]}>
        <boxGeometry args={[SCREEN_W + 0.4, SCREEN_H + 0.4, 0.22]} />
        <meshStandardMaterial color="#2a2f3a" metalness={0.4} roughness={0.5} />
      </mesh>
      {/* screen panel — transcript painted on as a texture */}
      <mesh position={[0, 0, 0.02]}>
        <planeGeometry args={[SCREEN_W, SCREEN_H]} />
        {texture ? (
          <meshBasicMaterial map={texture} toneMapped={false} />
        ) : (
          <meshBasicMaterial color="#ffffff" />
        )}
      </mesh>
      {/* support pole + foot */}
      <mesh position={[0, -3.7, -0.05]}>
        <cylinderGeometry args={[0.16, 0.16, 2.5, 16]} />
        <meshStandardMaterial color="#3a3f4a" metalness={0.5} roughness={0.5} />
      </mesh>
      <mesh position={[0, -4.92, -0.05]}>
        <cylinderGeometry args={[0.9, 1.05, 0.1, 24]} />
        <meshStandardMaterial color="#3a3f4a" metalness={0.5} roughness={0.5} />
      </mesh>
    </group>
  );
}

/**
 * Paint the transcript onto a 2D canvas and expose it as a Three texture.
 * Robust + crisp on the in-world screen (no drei Html-transform scaling to
 * calibrate). Shows the most recent turns that fit, newest at the bottom.
 */
function useTranscriptTexture(turns: MeetingTurn[], lastSeq: number): THREE.Texture | null {
  // Build a fresh, already-drawn CanvasTexture whenever the transcript
  // changes. All mutations target the locally-created canvas/texture, so
  // there is no hook-derived value being modified.
  const texture = useMemo(() => {
    if (typeof document === "undefined") return null;
    const canvas = document.createElement("canvas");
    canvas.width = CANVAS_W;
    canvas.height = CANVAS_H;
    drawTranscript(canvas, turns, lastSeq);
    const tex = new THREE.CanvasTexture(canvas);
    tex.colorSpace = THREE.SRGBColorSpace;
    return tex;
  }, [turns, lastSeq]);

  useEffect(() => () => texture?.dispose(), [texture]);

  return texture;
}

function wrapCanvasText(
  ctx: CanvasRenderingContext2D,
  text: string,
  maxWidth: number,
  maxLines: number,
): string[] {
  const words = text.split(/\s+/).filter(Boolean);
  const lines: string[] = [];
  let cur = "";
  for (const w of words) {
    const test = cur ? `${cur} ${w}` : w;
    if (ctx.measureText(test).width > maxWidth && cur) {
      lines.push(cur);
      cur = w;
      if (lines.length === maxLines) break;
    } else {
      cur = test;
    }
  }
  if (cur && lines.length < maxLines) lines.push(cur);
  if (lines.length === maxLines && cur && lines[maxLines - 1] !== cur) {
    let last = lines[maxLines - 1];
    while (last && ctx.measureText(`${last}…`).width > maxWidth) last = last.slice(0, -1);
    lines[maxLines - 1] = `${last}…`;
  }
  return lines;
}

function roundRect(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  r: number,
) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

function drawTranscript(
  canvas: HTMLCanvasElement,
  turns: MeetingTurn[],
  lastSeq: number,
) {
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const W = canvas.width;
  const H = canvas.height;
  const FONT = "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif";

  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, W, H);

  // ---- header ----
  const headerH = 70;
  ctx.fillStyle = "#f1f5f9";
  ctx.fillRect(0, 0, W, headerH);
  ctx.fillStyle = "#0ea5e9";
  ctx.beginPath();
  ctx.arc(34, headerH / 2, 9, 0, Math.PI * 2);
  ctx.fill();
  ctx.textBaseline = "middle";
  ctx.textAlign = "left";
  ctx.fillStyle = "#64748b";
  ctx.font = `600 26px ${FONT}`;
  ctx.fillText("LIVE TRANSCRIPT", 58, headerH / 2 + 1);
  ctx.textAlign = "right";
  ctx.fillStyle = "#94a3b8";
  ctx.font = `22px ${FONT}`;
  ctx.fillText(`${turns.length} turns`, W - 26, headerH / 2 + 1);
  ctx.textAlign = "left";

  const contentTop = headerH + 18;
  const contentBottom = H - 18;
  const sidePad = 24;
  const cardPadX = 20;
  const cardPadY = 16;
  const nameH = 26;
  const bodyLineH = 32;
  const gap = 14;
  const innerW = W - sidePad * 2 - cardPadX * 2;

  if (turns.length === 0) {
    ctx.fillStyle = "#94a3b8";
    ctx.font = `28px ${FONT}`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText("Waiting for the first turn…", W / 2, H / 2);
    return;
  }

  // Build blocks newest→oldest until the visible height budget is spent.
  ctx.textBaseline = "alphabetic";
  const budget = contentBottom - contentTop;
  type Block = { turn: MeetingTurn; lines: string[]; height: number; active: boolean };
  const blocks: Block[] = [];
  let used = 0;
  for (let i = turns.length - 1; i >= 0 && i >= turns.length - 20; i -= 1) {
    const t = turns[i];
    ctx.font = `400 25px ${FONT}`;
    const { preview } = humanize(t.content_preview || t.content_full);
    const lines = wrapCanvasText(ctx, preview, innerW, 5);
    const height = cardPadY * 2 + nameH + lines.length * bodyLineH;
    if (used + height + gap > budget && blocks.length > 0) break;
    blocks.unshift({ turn: t, lines, height, active: t.sequence === lastSeq });
    used += height + gap;
  }

  // Draw oldest→newest from the top so the newest sits at the bottom.
  let y = contentTop;
  for (const b of blocks) {
    const x = sidePad;
    const w = W - sidePad * 2;
    ctx.fillStyle = b.active ? "#e0f2fe" : "#f8fafc";
    roundRect(ctx, x, y, w, b.height, 12);
    ctx.fill();
    if (b.active) {
      ctx.strokeStyle = "#7dd3fc";
      ctx.lineWidth = 2;
      ctx.stroke();
    }
    // name
    ctx.fillStyle = "#64748b";
    ctx.font = `600 20px ${FONT}`;
    ctx.fillText(
      agentLabel(b.turn.agent_display_name, b.turn.agent_role),
      x + cardPadX,
      y + cardPadY + 18,
    );
    // body
    ctx.fillStyle = "#1e293b";
    ctx.font = `400 25px ${FONT}`;
    let ly = y + cardPadY + nameH + 22;
    for (const line of b.lines) {
      ctx.fillText(line, x + cardPadX, ly);
      ly += bodyLineH;
    }
    y += b.height + gap;
  }
}

function SeatNode({
  seat,
  emphasized,
  reducedMotion,
  streamText,
  streamSeq,
}: {
  seat: Seat;
  emphasized: boolean;
  reducedMotion: boolean;
  streamText: string;
  streamSeq: number;
}) {
  const tex = useAvatarTexture(seat.agent_role);
  const { x, y } = seatCoords(seat.seat_position, TABLE_RX + 0.9, TABLE_RZ + 0.9);
  const avatarGroup = useRef<THREE.Group>(null);
  const halo = useRef<THREE.Mesh>(null);

  useFrame((state) => {
    const target = emphasized ? 1.18 : 1;
    const g = avatarGroup.current;
    if (g) {
      const v = THREE.MathUtils.lerp(g.scale.x, target, 0.15);
      g.scale.setScalar(v);
    }
    if (halo.current && emphasized && !reducedMotion) {
      const pulse = 1 + Math.sin(state.clock.elapsedTime * 3) * 0.07;
      halo.current.scale.setScalar(pulse);
    }
  });

  const name = agentLabel(seat.agent_display_name, seat.agent_role);

  return (
    <group position={[x, 0, y]}>
      {/* stand / chair post */}
      <mesh position={[0, 0.35, 0]}>
        <cylinderGeometry args={[0.32, 0.42, 0.7, 24]} />
        <meshStandardMaterial
          color={emphasized ? "#0ea5e9" : "#475569"}
          emissive={emphasized ? "#0ea5e9" : "#000000"}
          emissiveIntensity={emphasized ? 0.35 : 0}
          roughness={0.6}
        />
      </mesh>

      <group ref={avatarGroup} position={[0, AVATAR_Y, 0]}>
        <Billboard>
          {emphasized && (
            <mesh ref={halo} position={[0, 0, -0.06]}>
              <ringGeometry args={[0.66, 0.78, 48]} />
              <meshBasicMaterial
                color="#0ea5e9"
                transparent
                opacity={0.85}
                side={THREE.DoubleSide}
              />
            </mesh>
          )}
          {tex && (
            <mesh>
              <planeGeometry args={[1.1, 1.1]} />
              <meshBasicMaterial map={tex} transparent toneMapped={false} />
            </mesh>
          )}
        </Billboard>
      </group>

      {/* Name plate */}
      <Html
        position={[0, AVATAR_Y + 0.8, 0]}
        center
        zIndexRange={[20, 0]}
        style={{ pointerEvents: "none" }}
      >
        <div className="pointer-events-none -translate-y-1/2 whitespace-nowrap rounded bg-[var(--bg-surface)]/90 px-1.5 py-0.5 text-center text-[10px] font-medium text-[var(--text-primary)] shadow">
          {name}
        </div>
      </Html>

      {/* Speech bubble — only the active speaker, streamed then collapsed. */}
      {emphasized && streamText && (
        <StreamingSpeechCard
          text={streamText}
          seqKey={streamSeq}
          reducedMotion={reducedMotion}
        />
      )}
    </group>
  );
}

/**
 * The active speaker's bubble. Reveals the dialogue with a typewriter
 * effect (driven off the render clock so it pauses with the scene), grows
 * + auto-scrolls as text arrives, then collapses a beat after it finishes.
 */
function StreamingSpeechCard({
  text,
  seqKey,
  reducedMotion,
}: {
  text: string;
  seqKey: number;
  reducedMotion: boolean;
}) {
  const [shown, setShown] = useState(reducedMotion ? text.length : 0);
  const [collapsed, setCollapsed] = useState(false);
  const progress = useRef(reducedMotion ? text.length : 0);
  const keyRef = useRef(seqKey);
  const doneAt = useRef<number | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useFrame((state, delta) => {
    // New turn for this same seat → restart the reveal.
    if (keyRef.current !== seqKey) {
      keyRef.current = seqKey;
      progress.current = reducedMotion ? text.length : 0;
      doneAt.current = null;
      setCollapsed(false);
      setShown(Math.floor(progress.current));
      return;
    }
    if (collapsed) return;
    if (progress.current < text.length) {
      progress.current = Math.min(text.length, progress.current + delta * STREAM_CHARS_PER_SEC);
      setShown(Math.floor(progress.current));
    } else if (doneAt.current == null) {
      doneAt.current = state.clock.elapsedTime;
    } else if (state.clock.elapsedTime - doneAt.current > HOLD_AFTER_DONE_S) {
      setCollapsed(true);
    }
  });

  // Keep the newest text in view as it streams in.
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [shown]);

  if (collapsed || !text) return null;
  const streaming = shown < text.length;

  return (
    <Html
      position={[0, AVATAR_Y + 1.25, 0]}
      center
      zIndexRange={[100, 50]}
      style={{ pointerEvents: "none" }}
    >
      <div className="pointer-events-none w-[240px] -translate-y-full rounded-lg border border-[var(--accent)] bg-[var(--bg-elevated)] px-2.5 py-2 text-left shadow-lg">
        <div ref={scrollRef} className="max-h-[140px] overflow-y-auto">
          <p className="whitespace-pre-wrap text-[11px] leading-snug text-[var(--text-primary)]">
            {text.slice(0, shown)}
            {streaming && <span className="ml-0.5 animate-pulse">▋</span>}
          </p>
        </div>
      </div>
    </Html>
  );
}

/**
 * Paint a dicebear SVG (same seed/variant the 2D <Avatar> uses) onto a
 * Three texture. Avoids suspense so a slow decode never blanks the scene.
 */
function useAvatarTexture(seed: string): THREE.Texture | null {
  const [tex, setTex] = useState<THREE.Texture | null>(null);
  useEffect(() => {
    let texture: THREE.Texture | null = null;
    const svg = buildAvatarSvg(seed, "active", 160);
    const uri = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
    const img = new Image();
    img.onload = () => {
      texture = new THREE.Texture(img);
      texture.colorSpace = THREE.SRGBColorSpace;
      texture.needsUpdate = true;
      setTex(texture);
    };
    img.src = uri;
    return () => {
      img.onload = null;
      texture?.dispose();
    };
  }, [seed]);
  return tex;
}

// ---------- Compact replay bar (drives the same hook as the 2D view) ----------

function Replay3DControls({ replay }: { replay: ReplayControls }) {
  const { revealed, total, playing, speed, onPlayPause, onRestart, onSkipToEnd, onSpeed } = replay;
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
