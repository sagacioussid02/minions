import { createAvatar } from "@dicebear/core";
import { bottts, lorelei, notionists, personas } from "@dicebear/collection";

export function Avatar({
  seed,
  size = 40,
  ring,
  mood = "active",
  className,
}: {
  seed: string;
  size?: number;
  ring?: string; // CSS color for a 1.5px halo, e.g. `var(--color-role-engineering)`
  mood?: "active" | "idle" | "leisure";
  className?: string;
}) {
  const palette = colorPalette(seed, mood);
  const svg = buildAvatarSvg(seed, mood, size);
  const accessory = mood === "active" ? null : idleAccessory(seed);

  return (
    <span
      className={`avatar-shell relative inline-flex shrink-0 items-center justify-center ${mood === "active" ? "avatar-active" : "avatar-leisure"} ${className ?? ""}`}
      style={{
        width: size,
        height: size,
        overflow: "visible",
      }}
    >
      <span
        className="absolute inset-0 rounded-full"
        style={{
          borderRadius: "50%",
          boxShadow: ring
            ? `0 0 0 2px ${ring}, 0 8px 22px color-mix(in srgb, ${ring} 38%, transparent)`
            : "0 8px 18px rgb(15 23 42 / 0.22)",
          overflow: "hidden",
          background: `linear-gradient(135deg, #${palette[0]}, #${palette[1] ?? palette[0]})`,
        }}
        dangerouslySetInnerHTML={{ __html: svg }}
      />
      {accessory && (
        <span
          className="avatar-accessory absolute -bottom-1 -right-1 flex items-center justify-center rounded-full border border-white/70 bg-white text-[10px] leading-none shadow-md"
          style={{ width: Math.max(16, size * 0.38), height: Math.max(16, size * 0.38) }}
          aria-hidden
        >
          {accessory}
        </span>
      )}
    </span>
  );
}

/**
 * Build the raw dicebear SVG string for a seed/mood. Shared by the 2D
 * <Avatar> and the 3D meeting room (which paints it onto a texture), so
 * an agent looks identical in both renderers.
 */
export function buildAvatarSvg(
  seed: string,
  mood: "active" | "idle" | "leisure" = "active",
  size = 96,
): string {
  const palette = colorPalette(seed, mood);
  const base = { seed, size, radius: 50, backgroundColor: palette };
  const variant = pickVariant(seed, mood);
  return variant === "notionists"
    ? createAvatar(notionists, base).toString()
    : variant === "personas"
      ? createAvatar(personas, base).toString()
      : variant === "lorelei"
        ? createAvatar(lorelei, base).toString()
        : createAvatar(bottts, base).toString();
}

function pickVariant(seed: string, mood: "active" | "idle" | "leisure") {
  if (mood === "leisure") return "notionists";
  if (mood === "idle") return "personas";
  const n = hash(seed) % 3;
  return n === 0 ? "bottts" : n === 1 ? "lorelei" : "personas";
}

function idleAccessory(seed: string): string {
  const choices = ["☕", "☕", "z", "♪"];
  return choices[hash(seed) % choices.length];
}

function colorPalette(seed: string, mood: "active" | "idle" | "leisure"): string[] {
  const palettes = [
    ["67e8f9", "a78bfa"],
    ["fde68a", "fb7185"],
    ["86efac", "38bdf8"],
    ["f0abfc", "f97316"],
    ["c4b5fd", "2dd4bf"],
    ["fca5a5", "93c5fd"],
  ];
  const picked = palettes[hash(seed) % palettes.length];
  if (mood === "leisure") return [...picked].reverse();
  return picked;
}

function hash(value: string): number {
  let h = 0;
  for (let i = 0; i < value.length; i += 1) {
    h = (h * 31 + value.charCodeAt(i)) >>> 0;
  }
  return h;
}
