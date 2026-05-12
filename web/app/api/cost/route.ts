import { NextResponse } from "next/server";
import { costSummary } from "@/lib/queries";
import { CostSummarySchema } from "@/lib/schemas";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const summary = await costSummary();
    return NextResponse.json(CostSummarySchema.parse(summary));
  } catch (err) {
    console.error("[/api/cost]", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "internal" },
      { status: 500 },
    );
  }
}
