/**
 * Anthropic dispatch for Surface B — TS twin of ``minions.agent_chat.chat``.
 *
 * One non-streaming Messages call per operator turn. Default model is Haiku
 * 4.5; CEO/CTO/MD seats use Sonnet 4.6. ``MINIONS_AGENT_CHAT_MODEL`` env
 * overrides everything (useful for ops + smoke tests).
 */

import Anthropic from "@anthropic-ai/sdk";
import { teamMemberLine, type ChatContext } from "./context";
import type { MessageRow } from "./repo";

export const DEFAULT_MODEL = "claude-haiku-4-5-20251001";
export const EXEC_MODEL = "claude-sonnet-4-6";
const EXEC_ROLES = new Set(["ceo", "cto", "managing_director"]);
const MAX_REPLY_TOKENS = 1024;
const COLD_START_HINT =
  "If the operator asks about specific past work and you don't see notes on it in your context, say so plainly — don't invent details.";

export type ChatReply = {
  text: string;
  model: string;
  promptTokens: number;
  responseTokens: number;
};

export function selectModelFor(ctx: ChatContext): string {
  const override = process.env.MINIONS_AGENT_CHAT_MODEL;
  if (override) return override;
  if (EXEC_ROLES.has(ctx.role)) return EXEC_MODEL;
  return DEFAULT_MODEL;
}

export function renderSystemPrompt(ctx: ChatContext): string {
  const parts: string[] = [ctx.persona.trimEnd()];

  if (ctx.dossierExcerpt) {
    parts.push(
      "# Project dossier (excerpt)\n" +
        "Use this as background on the project you work on. Do not quote it verbatim; refer to it naturally.\n\n" +
        ctx.dossierExcerpt.trimEnd(),
    );
  }

  if (ctx.teammates.length > 0) {
    const lines = [
      "# Your team",
      "These are the people you work with — your project teammates and the " +
        "leadership above you. Refer to them by name when relevant; you know " +
        "who they are and roughly what they do.",
      "",
      ...ctx.teammates.map(teamMemberLine),
    ];
    parts.push(lines.join("\n"));
  }

  if (ctx.learning.length > 0) {
    const lines = ["# Your active notes (most-confident first)"];
    ctx.learning.forEach((r, i) => {
      lines.push(`${i + 1}. (${r.kind}, ${r.confidence}) ${r.fact}`);
    });
    parts.push(lines.join("\n"));
  }

  if (ctx.transcriptSnippets.length > 0) {
    const lines = ["# Recent work you and teammates produced"];
    for (const m of ctx.transcriptSnippets) {
      const snippet = m.content.slice(0, 400).replace(/\n/g, " ").trim();
      lines.push(`- [${m.crew}/${m.agent_role}] ${snippet}`);
    }
    parts.push(lines.join("\n"));
  }

  if (ctx.coldStart) {
    parts.push(`# Note\n${COLD_START_HINT}`);
  }

  parts.push(
    `When the operator addresses you, answer in first person as ${ctx.displayName}. ` +
      "Keep replies tight — a few sentences unless the question genuinely needs more.",
  );

  return parts.join("\n\n");
}

export async function respond(args: {
  history: MessageRow[];
  userMessage: string;
  context: ChatContext;
  apiKey: string;
  model?: string;
  client?: Anthropic;
}): Promise<ChatReply> {
  const chosen = args.model ?? selectModelFor(args.context);
  const system = renderSystemPrompt(args.context);

  const messages = args.history.map((m) => ({
    role: m.role === "user" ? ("user" as const) : ("assistant" as const),
    content: m.content,
  }));
  messages.push({ role: "user" as const, content: args.userMessage });

  const client = args.client ?? new Anthropic({ apiKey: args.apiKey });
  const response = await client.messages.create({
    model: chosen,
    max_tokens: MAX_REPLY_TOKENS,
    system,
    messages,
  });

  const text = response.content
    .map((block) => (block.type === "text" ? block.text : ""))
    .join("")
    .trim();

  return {
    text,
    model: chosen,
    promptTokens: response.usage?.input_tokens ?? 0,
    responseTokens: response.usage?.output_tokens ?? 0,
  };
}
