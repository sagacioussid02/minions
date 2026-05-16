/**
 * Ambient drift particles — six tiny dots floating slowly across the canvas.
 *
 * Pure CSS, zero JS, server-renderable. The keyframes live in `globals.css`.
 * Disabled when `prefers-reduced-motion: reduce`.
 */
export function AmbientParticles() {
  // Deterministic positions so SSR + client agree; six dots staggered along
  // the y axis with different durations + delays for organic drift.
  const dots = [
    { y: 12, dur: 38, delay: 0,  size: 2.5 },
    { y: 27, dur: 46, delay: 6,  size: 1.5 },
    { y: 41, dur: 32, delay: 12, size: 3.0 },
    { y: 58, dur: 52, delay: 4,  size: 2.0 },
    { y: 73, dur: 40, delay: 18, size: 1.5 },
    { y: 88, dur: 48, delay: 9,  size: 2.5 },
  ];
  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden">
      {dots.map((d, i) => (
        <span
          key={i}
          className="ambient-dot"
          style={{
            top: `${d.y}%`,
            width: d.size,
            height: d.size,
            animationDuration: `${d.dur}s`,
            animationDelay: `-${d.delay}s`,
          }}
        />
      ))}
    </div>
  );
}
