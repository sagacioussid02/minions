import { NextRequest, NextResponse } from "next/server";
import { listRecentEvents } from "@/lib/queries";
import { ActivityEventSchema } from "@/lib/schemas";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  try {
    const { searchParams } = new URL(req.url);
    const limit = Number(searchParams.get("limit") ?? "100");
    const sinceId = searchParams.get("since")
      ? Number(searchParams.get("since"))
      : undefined;
    const project = searchParams.get("project") ?? undefined;
    const role = searchParams.get("role") ?? undefined;
    const event = searchParams.get("event") ?? undefined;
    const windowMinutesRaw = searchParams.get("window_minutes");
    const windowMinutes = windowMinutesRaw
      ? Math.min(Math.max(Number(windowMinutesRaw), 1), 24 * 60)
      : undefined;

    const events = await listRecentEvents({
      limit: Math.min(Math.max(limit, 1), 500),
      sinceId,
      project,
      role,
      event,
      windowMinutes,
    });
    const validated = events.map((e) => ActivityEventSchema.parse(e));
    return NextResponse.json({ events: validated });
  } catch (err) {
    console.error("[/api/events]", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "internal" },
      { status: 500 },
    );
  }
}
