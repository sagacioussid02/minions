/**
 * Render helpers for turning raw agent transcript content into something
 * a human can read at a glance — chat bubbles, transcript previews,
 * latest-turn panels all flow through these.
 *
 * The planning crew often outputs structured JSON (a sprint plan), so a
 * naked "first 140 chars" truncation produces things like
 * `{"goal":"Stabilize archi…` which is useless. `humanize()` detects JSON
 * and extracts a one-sentence summary (the `goal`, first feature title,
 * etc.) so the bubble actually says something.
 */

export interface HumanizedContent {
  /** One-line preview suitable for a chat bubble. */
  preview: string;
  /** Full prose body suitable for the transcript drawer. Cleaned but not
   *  truncated; for JSON inputs this is a multi-line pretty-printed version. */
  body: string;
  /** Whether the original content was structured JSON. */
  isJson: boolean;
}

const MAX_PREVIEW_CHARS = 160;

export function humanize(rawContent: string): HumanizedContent {
  const trimmed = (rawContent ?? "").trim();
  if (!trimmed) return { preview: "", body: "", isJson: false };

  // Heuristic detection: starts with { or [ AND parses cleanly.
  if (trimmed.startsWith("{") || trimmed.startsWith("[")) {
    try {
      const parsed = JSON.parse(trimmed);
      const preview = extractHeadline(parsed);
      const body = JSON.stringify(parsed, null, 2);
      return {
        preview: clamp(preview, MAX_PREVIEW_CHARS),
        body,
        isJson: true,
      };
    } catch {
      // Fall through to plain-text rendering.
    }
  }

  return {
    preview: clamp(trimmed, MAX_PREVIEW_CHARS),
    body: trimmed,
    isJson: false,
  };
}

/**
 * Walk a parsed planning-crew JSON object looking for the most
 * representative human-readable line. The planning crew typically yields
 * shapes like `{goal, features:[…], bugs:[…], tech_debt:[…]}` — `goal`
 * is the operator-facing one-liner.
 */
function extractHeadline(parsed: unknown): string {
  if (typeof parsed === "string") return parsed;
  if (Array.isArray(parsed)) {
    for (const item of parsed) {
      const inner = extractHeadline(item);
      if (inner) return inner;
    }
    return "(empty list)";
  }
  if (parsed && typeof parsed === "object") {
    const obj = parsed as Record<string, unknown>;
    const candidates = [
      "goal",
      "summary",
      "rationale",
      "title",
      "description",
      "verdict",
      "decision",
    ];
    for (const key of candidates) {
      const v = obj[key];
      if (typeof v === "string" && v.trim().length > 0) return v.trim();
    }
    // Try the first item of any list-shaped values (features, bugs, …).
    for (const key of ["features", "items", "bugs", "tech_debt", "plan_items"]) {
      const v = obj[key];
      if (Array.isArray(v) && v.length > 0) {
        const inner = extractHeadline(v[0]);
        if (inner) return inner;
      }
    }
    return "(structured plan)";
  }
  return "";
}

function clamp(s: string, max: number): string {
  const compact = s.replace(/\s+/g, " ").trim();
  if (compact.length <= max) return compact;
  const cut = compact.slice(0, max);
  const lastSpace = cut.lastIndexOf(" ");
  return (lastSpace > max * 0.6 ? cut.slice(0, lastSpace) : cut) + "…";
}
