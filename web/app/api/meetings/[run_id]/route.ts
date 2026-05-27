import { NextRequest, NextResponse } from "next/server";
import { getMeeting } from "@/lib/queries";
import { MeetingDetailSchema } from "@/lib/schemas";

export const dynamic = "force-dynamic";

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ run_id: string }> },
) {
  try {
    const { run_id } = await params;
    const meeting = await getMeeting(run_id);
    if (meeting == null) {
      return NextResponse.json({ error: "meeting not found" }, { status: 404 });
    }
    return NextResponse.json(MeetingDetailSchema.parse(meeting));
  } catch (err) {
    console.error("[/api/meetings/[run_id]]", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "internal" },
      { status: 500 },
    );
  }
}
