/**
 * GET → list chat threads for one agent, most recent first.
 */

import { NextResponse, type NextRequest } from "next/server";
import { listThreadsForAgent } from "@/lib/agent-chat/repo";
import { parseAgentId } from "@/lib/agent-chat/roster";
import { AgentChatThreadSchema } from "@/lib/agent-chat/schemas";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

type RouteParams = { params: Promise<{ agent_id: string }> };

export async function GET(_req: NextRequest, ctx: RouteParams): Promise<Response> {
  const { agent_id } = await ctx.params;
  if (!parseAgentId(agent_id)) {
    return NextResponse.json({ error: "invalid agent_id" }, { status: 400 });
  }
  try {
    const rows = await listThreadsForAgent(agent_id);
    const threads = rows.map((r) => AgentChatThreadSchema.parse(r));
    return NextResponse.json({ threads });
  } catch (err) {
    console.error("[/api/agents/.../threads GET]", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "internal" },
      { status: 500 },
    );
  }
}
