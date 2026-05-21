import { NextRequest, NextResponse } from "next/server";
import { createInterviewThread, listInterviewThreads } from "@/lib/spokesperson";
import { InterviewThreadSchema, SpokespersonThreadsSchema } from "@/lib/schemas";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  try {
    const { searchParams } = new URL(req.url);
    const project = searchParams.get("project");
    const threads = await listInterviewThreads(project || undefined);
    return NextResponse.json(SpokespersonThreadsSchema.parse({ threads }));
  } catch (err) {
    console.error("[/api/spokesperson/threads]", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "internal" },
      { status: 500 },
    );
  }
}

export async function POST(req: NextRequest) {
  try {
    const body = (await req.json()) as {
      project?: string | null;
      spokesperson_role?: string;
      title?: string | null;
    };
    const thread = await createInterviewThread({
      project: body.project,
      spokesperson_role: body.spokesperson_role || "cto",
      title: body.title,
    });
    return NextResponse.json(InterviewThreadSchema.parse(thread));
  } catch (err) {
    console.error("[POST /api/spokesperson/threads]", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "internal" },
      { status: 500 },
    );
  }
}
