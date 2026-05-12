import { NextResponse } from "next/server";
import { headlineCounters } from "@/lib/queries";
import { HeadlineCountersSchema } from "@/lib/schemas";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const counters = await headlineCounters();
    return NextResponse.json(HeadlineCountersSchema.parse(counters));
  } catch (err) {
    console.error("[/api/headline]", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "internal" },
      { status: 500 },
    );
  }
}
