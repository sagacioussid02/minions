/**
 * Recency → visual style for an agent card.
 *
 * Multi-stop gradient instead of binary idle/active. Even with stale data,
 * agents that fired in the last few hours stay visibly brighter than ones
 * that fired last week — so the Floor always has shape, not a uniform sea
 * of gray cards.
 */

export type Vitality = {
  /** 0..1 — drives saturation + opacity */
  brightness: number;
  /** css <filter> string; safe to pass directly to style */
  filter: string;
  /** Tier label so callers can choose halo / pulse */
  level: "live" | "fresh" | "warm" | "stale" | "cold";
  /** True iff caller should overlay the live pulse */
  showPulse: boolean;
};

const FRESH_MIN = 30;     // 30 min
const WARM_MIN = 2 * 60;  // 2h
const STALE_MIN = 24 * 60;// 24h

export function vitalityFromAge(ageMinutes: number | null): Vitality {
  if (ageMinutes == null) {
    return {
      brightness: 0.72,
      filter: "saturate(0.78) brightness(0.98)",
      level: "cold",
      showPulse: false,
    };
  }
  if (ageMinutes < 2) {
    // "live" — pulsing halo handled separately
    return {
      brightness: 1,
      filter: "none",
      level: "live",
      showPulse: true,
    };
  }
  if (ageMinutes < FRESH_MIN) {
    return {
      brightness: 0.95,
      filter: "saturate(1) brightness(1)",
      level: "fresh",
      showPulse: false,
    };
  }
  if (ageMinutes < WARM_MIN) {
    return {
      brightness: 0.75,
      filter: "saturate(0.85) brightness(0.95)",
      level: "warm",
      showPulse: false,
    };
  }
  if (ageMinutes < STALE_MIN) {
    return {
      brightness: 0.78,
      filter: "saturate(0.82) brightness(0.98)",
      level: "stale",
      showPulse: false,
    };
  }
  return {
    brightness: 0.72,
    filter: "saturate(0.75) brightness(0.95)",
    level: "cold",
    showPulse: false,
  };
}

export function ageMinutesFrom(isoTimestamp: string | null): number | null {
  if (!isoTimestamp) return null;
  const t = new Date(isoTimestamp).getTime();
  if (isNaN(t)) return null;
  return Math.max(0, (Date.now() - t) / 60_000);
}
