/**
 * Surface B — operator click-to-chat with a single agent.
 *
 * POST { thread_id?: string, message: string } → ChatPostResponse
 * GET  ?thread_id=<uuid> → { messages: AgentChatMessage[] }
 *
 * Threads + messages live in agent_chat_threads / agent_chat_messages — the
 * same tables the Python side uses, so either runtime can read either's writes.
 */

import { NextResponse, type NextRequest } from "next/server";
import { z } from "zod";
import { buildAgentContext } from "@/lib/agent-chat/context";
import { respond } from "@/lib/agent-chat/chat";
import {
  appendMessage,
  createThread,
  getThread,
  listMessages,
} from "@/lib/agent-chat/repo";
import { parseAgentId } from "@/lib/agent-chat/roster";
import {
  AgentChatMessageSchema,
  ChatPostBodySchema,
  ChatPostResponseSchema,
} from "@/lib/agent-chat/schemas";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

type RouteParams = { params: Promise<{ agent_id: string }> };

function deriveTitle(message: string): string {
  const oneLine = message.replace(/\s+/g, " ").trim();
  return oneLine.length > 80 ? `${oneLine.slice(0, 77)}…` : oneLine;
}

export async function POST(req: NextRequest, ctx: RouteParams): Promise<Response> {
  const { agent_id } = await ctx.params;
  const parsed = parseAgentId(agent_id);
  if (!parsed) {
    return NextResponse.json({ error: "invalid agent_id" }, { status: 400 });
  }

  let body: z.infer<typeof ChatPostBodySchema>;
  try {
    body = ChatPostBodySchema.parse(await req.json());
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "invalid body" },
      { status: 400 },
    );
  }

  const apiKey = process.env.ANTHROPIC_API_KEY;
  if (!apiKey) {
    return NextResponse.json(
      { error: "ANTHROPIC_API_KEY is not configured on this deployment" },
      { status: 503 },
    );
  }

  try {
    // Resolve or open the thread.
    let thread = body.thread_id ? await getThread(body.thread_id) : null;
    if (body.thread_id && !thread) {
      return NextResponse.json({ error: "thread not found" }, { status: 404 });
    }
    if (thread && thread.agent_id !== agent_id) {
      return NextResponse.json(
        { error: "thread belongs to a different agent" },
        { status: 409 },
      );
    }
    if (!thread) {
      thread = await createThread({
        agentId: agent_id,
        project: parsed.project,
        title: deriveTitle(body.message),
      });
    }

    // Build context + dispatch.
    const [context, history] = await Promise.all([
      buildAgentContext(parsed),
      listMessages(thread.id),
    ]);

    // Persist the user turn first so it's durable even if the LLM call fails.
    await appendMessage({
      threadId: thread.id,
      role: "user",
      content: body.message,
    });

    const reply = await respond({
      history,
      userMessage: body.message,
      context,
      apiKey,
    });

    const agentMsg = await appendMessage({
      threadId: thread.id,
      role: "agent",
      content: reply.text,
      model: reply.model,
      promptTokens: reply.promptTokens,
      responseTokens: reply.responseTokens,
    });

    const payload = ChatPostResponseSchema.parse({
      thread_id: thread.id,
      message_id: agentMsg.id,
      reply: reply.text,
      model: reply.model,
      prompt_tokens: reply.promptTokens,
      response_tokens: reply.responseTokens,
    });
    return NextResponse.json(payload);
  } catch (err) {
    console.error("[/api/agents/.../chat POST]", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "internal" },
      { status: 500 },
    );
  }
}

export async function GET(req: NextRequest, ctx: RouteParams): Promise<Response> {
  const { agent_id } = await ctx.params;
  const parsed = parseAgentId(agent_id);
  if (!parsed) {
    return NextResponse.json({ error: "invalid agent_id" }, { status: 400 });
  }

  const threadId = req.nextUrl.searchParams.get("thread_id");
  if (!threadId) {
    return NextResponse.json({ error: "thread_id query param required" }, { status: 400 });
  }

  try {
    const thread = await getThread(threadId);
    if (!thread) {
      return NextResponse.json({ error: "thread not found" }, { status: 404 });
    }
    if (thread.agent_id !== agent_id) {
      return NextResponse.json(
        { error: "thread belongs to a different agent" },
        { status: 409 },
      );
    }
    const rows = await listMessages(threadId);
    const messages = rows.map((r) => AgentChatMessageSchema.parse(r));
    return NextResponse.json({ messages });
  } catch (err) {
    console.error("[/api/agents/.../chat GET]", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "internal" },
      { status: 500 },
    );
  }
}
