import { NextRequest, NextResponse } from "next/server";
import { getInterviewBundle } from "@/lib/spokesperson";
import { ConsultationSchema } from "@/lib/schemas";

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
      consultations: bundle.consultations.map((item) => ConsultationSchema.parse(item)),
    });
  } catch (err) {
    console.error("[/api/spokesperson/threads/:id/consultations]", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "internal" },
      { status: 500 },
    );
  }
}
