import { sql } from "@/lib/db";
import { getMeeting } from "@/lib/queries";
import { MeetingDetailSchema, MeetingTurnSchema } from "@/lib/schemas";

export const runtime = "edge";
export const dynamic = "force-dynamic";

/**
 * Server-Sent Events stream for a single meeting run.
 *
 * Resolution Q7 of the living-org-spaces proposal: SSE over polling so the
 * round-table feels alive. Client uses native EventSource (auto-reconnect)
 * and listens for three event kinds:
 *
 *   - `init`      — full MeetingDetail JSON, sent once on connect
 *   - `turn`      — MeetingTurn JSON, sent each time a new turn lands
 *   - `heartbeat` — empty data, sent every HEARTBEAT_MS so proxies + the
 *                   Vercel function don't close an idle connection
 *
 * Polls the DB every POLL_MS for turns with sequence > last seen. Cheap —
 * a single indexed SELECT against crew_transcripts. Total cost: ~one PG
 * query per 3s while a client is connected.
 */

const POLL_MS = 3_000;
const HEARTBEAT_MS = 15_000;
const MAX_DURATION_MS = 9 * 60_000; // close before Vercel's 10-min Edge cap
const ENCODER = new TextEncoder();

function sse(event: string, data: unknown): Uint8Array {
  // SSE frame format: each field on its own line, blank line terminates.
  const payload = typeof data === "string" ? data : JSON.stringify(data);
  return ENCODER.encode(`event: ${event}\ndata: ${payload}\n\n`);
}

async function fetchTurnsSince(
  runId: string,
  lastSequence: number,
): Promise<Array<Record<string, unknown>>> {
  const s = sql();
  const rows = (await s`
    SELECT payload
    FROM crew_transcripts
    WHERE run_id = ${runId} AND sequence > ${lastSequence}
    ORDER BY sequence ASC
    LIMIT 50
  `) as Array<{ payload: Record<string, unknown> }>;
  return rows.map((r) => r.payload);
}

function mapTurnPayload(payload: Record<string, unknown>) {
  const full = String(payload.content ?? "");
  return {
    sequence:
      typeof payload.sequence === "number"
        ? payload.sequence
        : Number(payload.sequence ?? 0),
    agent_role: String(payload.agent_role ?? ""),
    agent_display_name:
      payload.agent_display_name == null ? null : String(payload.agent_display_name),
    role_in_conversation: String(payload.role_in_conversation ?? "other"),
    content_preview: full.length > 280 ? full.slice(0, 279) + "…" : full,
    content_full: full,
    created_at: String(payload.created_at ?? new Date().toISOString()),
  };
}

export async function GET(
  req: Request,
  { params }: { params: Promise<{ run_id: string }> },
) {
  const { run_id } = await params;

  // Pull the full meeting once at connect time. If the run doesn't exist
  // we return a one-shot SSE error stream so the client gets a clean signal.
  const initial = await getMeeting(run_id);
  if (!initial) {
    return new Response(
      `event: error\ndata: ${JSON.stringify({ error: "meeting not found" })}\n\n`,
      { status: 404, headers: { "Content-Type": "text/event-stream" } },
    );
  }

  let lastSequence =
    initial.turns.length > 0 ? initial.turns[initial.turns.length - 1].sequence : -1;
  const initialPayload = MeetingDetailSchema.parse(initial);

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

      // Watch for client disconnect.
      req.signal.addEventListener("abort", close);

      // Send initial state.
      controller.enqueue(sse("init", initialPayload));

      const start = Date.now();
      let lastHeartbeat = start;

      // Polling loop. The Neon serverless driver is HTTP under the hood, so
      // each iteration is a stateless query — no long-held DB connection.
      while (!closed && Date.now() - start < MAX_DURATION_MS) {
        await new Promise((r) => setTimeout(r, POLL_MS));
        if (closed) break;
        try {
          const newPayloads = await fetchTurnsSince(run_id, lastSequence);
          for (const payload of newPayloads) {
            const turn = mapTurnPayload(payload);
            const validated = MeetingTurnSchema.parse(turn);
            controller.enqueue(sse("turn", validated));
            lastSequence = validated.sequence;
          }
        } catch (err) {
          // Don't blow up the stream on a transient DB hiccup; the next
          // tick will try again. Log via console for Vercel's log surface.
          console.error("[/api/meetings/[run_id]/stream] poll error", err);
        }
        if (Date.now() - lastHeartbeat >= HEARTBEAT_MS) {
          controller.enqueue(sse("heartbeat", {}));
          lastHeartbeat = Date.now();
        }
      }
      // Soft close — client will reconnect automatically via EventSource.
      close();
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no", // disable buffering on proxies
    },
  });
}
