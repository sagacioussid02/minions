import { NextResponse } from "next/server";
import { listActiveAgents } from "@/lib/queries";
import { AgentStateSchema } from "@/lib/schemas";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const agents = await listActiveAgents();
    const validated = agents.map((a) => AgentStateSchema.parse(a));
    return NextResponse.json({ agents: validated });
  } catch (err) {
    console.error("[/api/agents]", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "internal" },
      { status: 500 },
    );
  }
}
