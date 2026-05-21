import { NextRequest, NextResponse } from "next/server";
import { listTranscriptByRun, listTranscriptsForProject } from "@/lib/queries";
import { CrewTranscriptMessageSchema } from "@/lib/schemas";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  try {
    const { searchParams } = new URL(req.url);
    const runId = searchParams.get("run_id");
    const project = searchParams.get("project");
    const limit = Number(searchParams.get("limit") ?? "50");

    if (!runId && !project) {
      return NextResponse.json(
        { error: "provide run_id or project" },
        { status: 400 },
      );
    }

    const messages = runId
      ? await listTranscriptByRun(runId)
      : await listTranscriptsForProject(project!, limit);

    return NextResponse.json({
      messages: messages.map((m) => CrewTranscriptMessageSchema.parse(m)),
    });
  } catch (err) {
    console.error("[/api/transcripts]", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "internal" },
      { status: 500 },
    );
  }
}
