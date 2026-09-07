import { NextResponse } from "next/server";
import { listSiteHealth } from "@/lib/queries";
import { SiteHealthSchema } from "@/lib/schemas";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const data = await listSiteHealth();
    return NextResponse.json(SiteHealthSchema.parse(data));
  } catch (err) {
    console.error("[/api/site-health]", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "internal" },
      { status: 500 },
    );
  }
}
