import { NextRequest, NextResponse } from "next/server";
import { askSpokesperson } from "@/lib/spokesperson";
import { SpokespersonAnswerSchema } from "@/lib/schemas";

export const dynamic = "force-dynamic";

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  try {
    const { id } = await params;
    const body = (await req.json()) as {
      question?: string;
      project?: string | null;
      spokesperson_role?: string;
    };
    const question = body.question?.trim();
    if (!question) {
      return NextResponse.json({ error: "question is required" }, { status: 400 });
    }
    const answer = await askSpokesperson({
      thread_id: id === "new" ? null : id,
      project: body.project,
      spokesperson_role: body.spokesperson_role || "cto",
      question,
    });
    return NextResponse.json(SpokespersonAnswerSchema.parse(answer));
  } catch (err) {
    console.error("[POST /api/spokesperson/threads/:id/messages]", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "internal" },
      { status: 500 },
    );
  }
}
