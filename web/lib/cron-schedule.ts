/**
 * Tiny cron-expression evaluator for the patterns the minions org actually
 * uses. NOT a general-purpose cron parser — only the forms below.
 *
 * Source of truth for what runs when: `.github/workflows/*.yml` in the
 * private `minions` repo. Keep this table in sync when adding a new cron.
 *
 * Supported field syntax:
 *   - literal:    "5"
 *   - wildcard:   "*"
 *   - every N:    star-slash-N  (e.g. star-slash-20 minutes, star-slash-6 hours)
 *   - list:       "15,45"
 *   - range:      "1-7"
 *
 * Day-of-month + day-of-week + month always wildcard or simple — we do not
 * combine restrictive DOM and DOW.
 */

export type MinionsCron =
  | "execute-approved"
  | "execute-expedited"
  | "pr-followup"
  | "pr-review-loop"
  | "daily"
  | "weekly"
  | "friday"
  | "monthly"
  | "scrum"
  | "crew-heartbeat";

// Mirrors the schedules in `.github/workflows/*.yml`. UTC.
export const CRON_SCHEDULES: Record<MinionsCron, { expr: string; label: string }> = {
  "execute-approved":  { expr: "15 */6 * * *",   label: "Engineer pickup (regular)" },
  "execute-expedited": { expr: "*/10 * * * *",   label: "Engineer pickup (expedited)" },
  "pr-followup":       { expr: "*/30 * * * *",   label: "PR follow-up sweep" },
  "pr-review-loop":    { expr: "15,45 * * * *",  label: "PR review loop" },
  "daily":             { expr: "0 */6 * * *",    label: "Daily monitor" },
  "weekly":            { expr: "0 9 * * 1",      label: "Weekly planning" },
  "friday":            { expr: "0 16 * * 5",     label: "Friday digest" },
  "monthly":           { expr: "0 9 1-7 * 1",    label: "Monthly portfolio review" },
  "scrum":             { expr: "0 14 */2 * *",   label: "Scrum cadence" },
  "crew-heartbeat":    { expr: "30 13 * * *",    label: "Crew heartbeat" },
};

interface ParsedCron {
  minute: (m: number) => boolean;
  hour: (h: number) => boolean;
  dom: (d: number) => boolean;
  dow: (d: number) => boolean;  // 0 = Sun
}

function parseField(field: string, min: number): (n: number) => boolean {
  if (field === "*") return () => true;
  if (field.startsWith("*/")) {
    const step = Number(field.slice(2));
    return (n) => (n - min) % step === 0;
  }
  if (field.includes(",")) {
    const allowed = new Set(field.split(",").map(Number));
    return (n) => allowed.has(n);
  }
  if (field.includes("-")) {
    const [lo, hi] = field.split("-").map(Number);
    return (n) => n >= lo && n <= hi;
  }
  const exact = Number(field);
  return (n) => n === exact;
}

function parseCron(expr: string): ParsedCron {
  const [m, h, dom, _mon, dow] = expr.trim().split(/\s+/);
  return {
    minute: parseField(m, 0),
    hour: parseField(h, 0),
    dom: parseField(dom, 1),
    dow: parseField(dow, 0),
  };
}

/**
 * Next firing time (UTC) of `expr` strictly after `from` (default: now).
 * Returns null if no match found within 31 days (suggests a misconfigured
 * expression — caller can fall back to "scheduled" without a timestamp).
 */
export function nextCronTick(expr: string, from: Date = new Date()): Date | null {
  const parsed = parseCron(expr);
  const candidate = new Date(from.getTime());
  candidate.setUTCSeconds(0, 0);
  candidate.setUTCMinutes(candidate.getUTCMinutes() + 1);  // strictly after

  const cap = new Date(from.getTime() + 31 * 24 * 60 * 60 * 1000);
  while (candidate <= cap) {
    if (
      parsed.minute(candidate.getUTCMinutes()) &&
      parsed.hour(candidate.getUTCHours()) &&
      parsed.dom(candidate.getUTCDate()) &&
      parsed.dow(candidate.getUTCDay())
    ) {
      return candidate;
    }
    candidate.setUTCMinutes(candidate.getUTCMinutes() + 1);
  }
  return null;
}

/** Human-readable "in 2h 14m" / "in 9m" / "now". */
export function formatTimeUntil(target: Date, from: Date = new Date()): string {
  const diffMs = target.getTime() - from.getTime();
  if (diffMs <= 0) return "now";
  const totalMin = Math.round(diffMs / 60_000);
  if (totalMin < 60) return `in ${totalMin}m`;
  const hours = Math.floor(totalMin / 60);
  const mins = totalMin % 60;
  return mins === 0 ? `in ${hours}h` : `in ${hours}h ${mins}m`;
}

/** "21:15 UTC (in 2h 14m)" — the format embedded in tooltips and footers. */
export function describeNextRun(expr: string, from: Date = new Date()): string {
  const next = nextCronTick(expr, from);
  if (!next) return "schedule unknown";
  const hh = String(next.getUTCHours()).padStart(2, "0");
  const mm = String(next.getUTCMinutes()).padStart(2, "0");
  return `${hh}:${mm} UTC (${formatTimeUntil(next, from)})`;
}
