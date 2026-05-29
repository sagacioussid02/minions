import { NextRequest, NextResponse } from "next/server";
import { listMeetings } from "@/lib/queries";
import { MeetingListSchema } from "@/lib/schemas";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  try {
    const { searchParams } = new URL(req.url);
    const project = searchParams.get("project") ?? undefined;
    const windowParam = searchParams.get("window");
    const windowMinutes = windowParam ? Math.min(Math.max(Number(windowParam), 5), 7 * 24 * 60) : undefined;
    const meetings = await listMeetings({ windowMinutes, project });
    return NextResponse.json(MeetingListSchema.parse({ meetings }));
  } catch (err) {
    console.error("[/api/meetings]", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "internal" },
      { status: 500 },
    );
  }
}
