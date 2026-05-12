import { NextResponse } from "next/server";
import { listOpenQuestions } from "@/lib/queries";
import { QuestionSchema } from "@/lib/schemas";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const qs = await listOpenQuestions();
    const validated = qs.map((q) => QuestionSchema.parse(q));
    return NextResponse.json({ questions: validated });
  } catch (err) {
    console.error("[/api/questions]", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "internal" },
      { status: 500 },
    );
  }
}
