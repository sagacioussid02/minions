import { NextRequest, NextResponse } from "next/server";
import { listSprintBoard } from "@/lib/queries";
import { SprintBoardSchema, SprintWindowSchema } from "@/lib/schemas";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  try {
    const { searchParams } = new URL(req.url);
    const project = searchParams.get("project") ?? undefined;
    const rawWindow = searchParams.get("window") ?? "this_week";
    const window = SprintWindowSchema.catch("this_week").parse(rawWindow);
    const board = await listSprintBoard(project, window);
    return NextResponse.json(SprintBoardSchema.parse(board));
  } catch (err) {
    console.error("[/api/sprint-board]", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "internal" },
      { status: 500 },
    );
  }
}
