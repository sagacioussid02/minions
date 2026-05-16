import { NextResponse } from "next/server";
import { listOpenWorkItems } from "@/lib/queries";
import { WorkItemSchema } from "@/lib/schemas";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const items = await listOpenWorkItems();
    const validated = items.map((i) => WorkItemSchema.parse(i));
    return NextResponse.json({ items: validated });
  } catch (err) {
    console.error("[/api/work-items]", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "internal" },
      { status: 500 },
    );
  }
}
