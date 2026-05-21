import { NextRequest, NextResponse } from "next/server";
import { createInterviewTask, getInterviewBundle } from "@/lib/spokesperson";
import { InterviewTaskProposalSchema } from "@/lib/schemas";

export const dynamic = "force-dynamic";

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  try {
    const { id } = await params;
    const bundle = await getInterviewBundle(id);
    if (!bundle) {
      return NextResponse.json({ error: "thread not found" }, { status: 404 });
    }
    return NextResponse.json({
      tasks: bundle.tasks.map((item) => InterviewTaskProposalSchema.parse(item)),
    });
  } catch (err) {
    console.error("[/api/spokesperson/threads/:id/tasks]", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "internal" },
      { status: 500 },
    );
  }
}

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  try {
    const { id } = await params;
    const body = (await req.json()) as {
      message_id?: string | null;
      owner_role?: string;
      title?: string;
      rationale?: string | null;
    };
    if (!body.title?.trim()) {
      return NextResponse.json({ error: "title is required" }, { status: 400 });
    }
    const task = await createInterviewTask({
      thread_id: id,
      message_id: body.message_id,
      owner_role: body.owner_role || "manager",
      title: body.title,
      rationale: body.rationale,
    });
    return NextResponse.json(InterviewTaskProposalSchema.parse(task));
  } catch (err) {
    console.error("[POST /api/spokesperson/threads/:id/tasks]", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "internal" },
      { status: 500 },
    );
  }
}
