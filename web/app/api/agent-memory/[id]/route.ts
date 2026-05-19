import { NextRequest, NextResponse } from "next/server";
import { listAgentMemory } from "@/lib/queries";
import { AgentMemorySchema } from "@/lib/schemas";

export const dynamic = "force-dynamic";

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  try {
    const { id } = await params;
    const { searchParams } = new URL(req.url);
    const includeCold = searchParams.get("include_cold") === "true";
    const memory = await listAgentMemory(decodeURIComponent(id), includeCold);
    return NextResponse.json({
      memory: memory.map((record) => AgentMemorySchema.parse(record)),
    });
  } catch (err) {
    console.error("[/api/agent-memory/:id]", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "internal" },
      { status: 500 },
    );
  }
}
