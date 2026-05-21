import { NextRequest, NextResponse } from "next/server";
import { listAgilePanel } from "@/lib/queries";
import { AgilePanelSchema } from "@/lib/schemas";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  try {
    const { searchParams } = new URL(req.url);
    const project = searchParams.get("project") ?? undefined;
    const panel = await listAgilePanel(project);
    return NextResponse.json(AgilePanelSchema.parse(panel));
  } catch (err) {
    console.error("[/api/agile]", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "internal" },
      { status: 500 },
    );
  }
}
