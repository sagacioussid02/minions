import { listActiveAgents } from "@/lib/queries";
import { AgentStateSchema } from "@/lib/schemas";

export const runtime = "edge";
export const dynamic = "force-dynamic";

/**
 * Server-Sent Events stream for live agent presence (portfolio-wide, not
 * per-run — no dynamic segment). Mirrors the pattern in
 * app/api/meetings/[run_id]/stream/route.ts:
 *
 *   - `init`             — full AgentState[] JSON, sent once on connect
 *   - `presence_update`  — AgentState[] JSON, only the agents whose
 *                          status/detail/in_flight changed since the last tick
 *   - `heartbeat`        — empty data, sent every HEARTBEAT_MS
 *
 * Polls listActiveAgents() every POLL_MS (same tenant-scoped query the
 * plain /api/agents route uses) and diffs against the last-sent state per
 * agent id, so idle agents don't re-send on every tick.
 */

const POLL_MS = 3_000;
const HEARTBEAT_MS = 15_000;
const MAX_DURATION_MS = 9 * 60_000; // close before Vercel's 10-min Edge cap
const ENCODER = new TextEncoder();

function sse(event: string, data: unknown): Uint8Array {
  const payload = typeof data === "string" ? data : JSON.stringify(data);
  return ENCODER.encode(`event: ${event}\ndata: ${payload}\n\n`);
}

function presenceKey(agent: {
  status: string;
  detail: string | null;
  in_flight: boolean;
}): string {
  return `${agent.status}|${agent.detail ?? ""}|${agent.in_flight}`;
}

export async function GET(req: Request) {
  const initial = (await listActiveAgents()).map((a) => AgentStateSchema.parse(a));
  const lastSent = new Map<string, string>(initial.map((a) => [a.id, presenceKey(a)]));

  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      let closed = false;
      const close = () => {
        if (closed) return;
        closed = true;
        try {
          controller.close();
        } catch {
          // Already closed by client disconnect — fine.
        }
      };
      req.signal.addEventListener("abort", close);

      controller.enqueue(sse("init", initial));

      const start = Date.now();
      let lastHeartbeat = start;

      // Polling loop — Neon's HTTP driver means each tick is a stateless
      // query, no long-held DB connection.
      while (!closed && Date.now() - start < MAX_DURATION_MS) {
        await new Promise((r) => setTimeout(r, POLL_MS));
        if (closed) break;
        try {
          const agents = (await listActiveAgents()).map((a) => AgentStateSchema.parse(a));
          const changed = agents.filter((a) => lastSent.get(a.id) !== presenceKey(a));
          if (changed.length > 0) {
            for (const a of changed) lastSent.set(a.id, presenceKey(a));
            controller.enqueue(sse("presence_update", changed));
          }
        } catch (err) {
          console.error("[/api/presence/stream] poll error", err);
        }
        if (Date.now() - lastHeartbeat >= HEARTBEAT_MS) {
          controller.enqueue(sse("heartbeat", {}));
          lastHeartbeat = Date.now();
        }
      }
      close();
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no",
    },
  });
}
