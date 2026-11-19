import { NextRequest, NextResponse } from "next/server";
import { rejectDecision } from "@/lib/mutations";

export const dynamic = "force-dynamic";

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  try {
    const { id } = await params;
    let body: { reason?: string } = {};
    try {
      body = await req.json();
    } catch {
      /* no body is fine */
    }
    const changed = await rejectDecision(id, body.reason);
    if (!changed) {
      return NextResponse.json(
        { error: "decision not found or no longer pending", id },
        { status: 404 },
      );
    }
    return NextResponse.json({ ok: true, id });
  } catch (err) {
    console.error("[reject]", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "internal" },
      { status: 500 },
    );
  }
}
