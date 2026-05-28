/**
 * Zod schemas for Surface B chat endpoints. Match the Python pydantic models
 * in ``minions.models.agent_chat`` so both sides serialise identically.
 */

import { z } from "zod";

export const ChatMessageRoleSchema = z.enum(["user", "agent"]);

export const AgentChatMessageSchema = z.object({
  id: z.string(),
  thread_id: z.string(),
  role: ChatMessageRoleSchema,
  content: z.string(),
  created_at: z.string(),
  model: z.string().nullable(),
  prompt_tokens: z.number().int().nullable(),
  response_tokens: z.number().int().nullable(),
});
export type AgentChatMessage = z.infer<typeof AgentChatMessageSchema>;

export const AgentChatThreadSchema = z.object({
  id: z.string(),
  agent_id: z.string(),
  project: z.string().nullable(),
  title: z.string().nullable(),
  created_at: z.string(),
  last_message_at: z.string(),
});
export type AgentChatThread = z.infer<typeof AgentChatThreadSchema>;

// ---------- Route I/O ----------

export const ChatPostBodySchema = z.object({
  thread_id: z.string().uuid().optional(),
  message: z.string().trim().min(1).max(4000),
});
export type ChatPostBody = z.infer<typeof ChatPostBodySchema>;

export const ChatPostResponseSchema = z.object({
  thread_id: z.string(),
  message_id: z.string(),
  reply: z.string(),
  model: z.string(),
  prompt_tokens: z.number().int(),
  response_tokens: z.number().int(),
});
export type ChatPostResponse = z.infer<typeof ChatPostResponseSchema>;
