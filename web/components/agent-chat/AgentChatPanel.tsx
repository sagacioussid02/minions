"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Avatar } from "@/components/Avatar";
import type { AgentState } from "@/lib/schemas";
import { roleShortLabel } from "@/lib/roles";

const TIER_RING: Record<string, string> = {
  executive: "var(--color-role-executive, #fbbf24)",
  engineering: "var(--color-role-engineering, #22d3ee)",
  audit: "var(--color-role-audit, #e879f9)",
  specialist: "var(--color-role-specialist, #34d399)",
};

type ThreadRow = {
  id: string;
  agent_id: string;
  project: string | null;
  title: string | null;
  created_at: string;
  last_message_at: string;
};

type MessageRow = {
  id: string;
  thread_id: string;
  role: "user" | "agent";
  content: string;
  created_at: string;
  model: string | null;
  prompt_tokens: number | null;
  response_tokens: number | null;
};

type ChatPostResponse = {
  thread_id: string;
  message_id: string;
  reply: string;
  model: string;
  prompt_tokens: number;
  response_tokens: number;
};

async function fetchThreads(agentId: string): Promise<ThreadRow[]> {
  const r = await fetch(
    `/api/agents/${encodeURIComponent(agentId)}/threads`,
    { cache: "no-store" },
  );
  if (!r.ok) throw new Error(`threads fetch failed (${r.status})`);
  const body = (await r.json()) as { threads: ThreadRow[] };
  return body.threads;
}

async function fetchMessages(agentId: string, threadId: string): Promise<MessageRow[]> {
  const r = await fetch(
    `/api/agents/${encodeURIComponent(agentId)}/chat?thread_id=${encodeURIComponent(threadId)}`,
    { cache: "no-store" },
  );
  if (!r.ok) throw new Error(`messages fetch failed (${r.status})`);
  const body = (await r.json()) as { messages: MessageRow[] };
  return body.messages;
}

async function postChat(args: {
  agentId: string;
  threadId: string | null;
  message: string;
}): Promise<ChatPostResponse> {
  const r = await fetch(`/api/agents/${encodeURIComponent(args.agentId)}/chat`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      thread_id: args.threadId ?? undefined,
      message: args.message,
    }),
  });
  if (!r.ok) {
    const errBody = (await r.json().catch(() => ({}))) as { error?: string };
    throw new Error(errBody.error ?? `chat post failed (${r.status})`);
  }
  return (await r.json()) as ChatPostResponse;
}

export function AgentChatPanel({
  agent,
  onClose,
}: {
  agent: AgentState;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  // Explicit user selection (clicking a thread from history). When null, the
  // active thread falls back to the most-recent thread on file, unless
  // `wantsNewThread` is true (operator clicked "new thread").
  const [selectedThreadId, setSelectedThreadId] = useState<string | null>(null);
  const [wantsNewThread, setWantsNewThread] = useState(false);
  const [draft, setDraft] = useState("");
  const [showThreadList, setShowThreadList] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scrollerRef = useRef<HTMLDivElement | null>(null);

  const threadsQuery = useQuery({
    queryKey: ["agent-chat-threads", agent.id],
    queryFn: () => fetchThreads(agent.id),
  });

  const activeThreadId = useMemo<string | null>(() => {
    if (wantsNewThread) return null;
    if (selectedThreadId) return selectedThreadId;
    const list = threadsQuery.data;
    return list && list.length > 0 ? list[0].id : null;
  }, [wantsNewThread, selectedThreadId, threadsQuery.data]);

  const messagesQuery = useQuery({
    queryKey: ["agent-chat-messages", agent.id, activeThreadId ?? "new"],
    queryFn: () =>
      activeThreadId ? fetchMessages(agent.id, activeThreadId) : Promise.resolve([]),
    enabled: Boolean(activeThreadId),
  });

  const sendMutation = useMutation({
    mutationFn: postChat,
    onSuccess: (resp) => {
      setSelectedThreadId(resp.thread_id);
      setWantsNewThread(false);
      qc.invalidateQueries({ queryKey: ["agent-chat-threads", agent.id] });
      qc.invalidateQueries({
        queryKey: ["agent-chat-messages", agent.id, resp.thread_id],
      });
    },
    onError: (e: Error) => setError(e.message),
  });

  // Auto-scroll on new content.
  useEffect(() => {
    const el = scrollerRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [messagesQuery.data, sendMutation.isPending]);

  // Esc to close.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const messages = useMemo<MessageRow[]>(
    () => messagesQuery.data ?? [],
    [messagesQuery.data],
  );
  const displayName = agent.display_name?.trim() || roleShortLabel(agent.role);
  const ring = TIER_RING[agent.role_tier] ?? "var(--accent)";

  // Build the rendered list — real messages + optimistic in-flight pair.
  const rendered = useMemo<MessageRow[]>(() => {
    if (!sendMutation.isPending || !sendMutation.variables) return messages;
    const pendingMsg = sendMutation.variables.message;
    const synthetic: MessageRow[] = [
      {
        id: "optimistic-user",
        thread_id: activeThreadId ?? "pending",
        role: "user",
        content: pendingMsg,
        created_at: new Date().toISOString(),
        model: null,
        prompt_tokens: null,
        response_tokens: null,
      },
      {
        id: "optimistic-agent",
        thread_id: activeThreadId ?? "pending",
        role: "agent",
        content: "…thinking",
        created_at: new Date().toISOString(),
        model: null,
        prompt_tokens: null,
        response_tokens: null,
      },
    ];
    return [...messages, ...synthetic];
  }, [messages, sendMutation.isPending, sendMutation.variables, activeThreadId]);

  const lastModel = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      if (messages[i].role === "agent" && messages[i].model) return messages[i].model;
    }
    return null;
  }, [messages]);

  function onSend() {
    const trimmed = draft.trim();
    if (!trimmed || sendMutation.isPending) return;
    setError(null);
    sendMutation.mutate({
      agentId: agent.id,
      threadId: activeThreadId,
      message: trimmed,
    });
    setDraft("");
  }

  return (
    <>
      <div
        className="fixed inset-0 z-40 bg-black/50 backdrop-blur-sm"
        onClick={onClose}
        aria-hidden
      />
      <aside
        className="fixed inset-y-0 right-0 z-50 flex w-full max-w-md flex-col border-l border-[var(--line)] bg-[var(--surface-0)] shadow-2xl sm:max-w-lg"
        role="dialog"
        aria-label={`Chat with ${displayName}`}
      >
        {/* Header */}
        <div className="flex items-start gap-3 border-b border-[var(--line)] p-4">
          <Avatar seed={agent.id} size={48} ring={ring} mood="active" />
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-semibold">{displayName}</div>
            <div className="truncate text-[11px] uppercase tracking-wider text-[var(--text-muted)]">
              {roleShortLabel(agent.role)}
              {agent.project ? ` · ${agent.project}` : " · portfolio"}
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded p-1 text-[var(--text-muted)] transition hover:bg-[var(--surface-1)] hover:text-[var(--text-primary)]"
            aria-label="Close chat"
          >
            ✕
          </button>
        </div>

        {/* Thread switcher */}
        <div className="flex items-center justify-between gap-2 border-b border-[var(--line)] px-4 py-2 text-[11px]">
          <button
            type="button"
            onClick={() => setShowThreadList((v) => !v)}
            className="text-[var(--text-muted)] underline-offset-2 hover:text-[var(--text-primary)] hover:underline"
          >
            {showThreadList ? "hide threads" : "past threads"}
            {threadsQuery.data ? ` (${threadsQuery.data.length})` : ""}
          </button>
          <button
            type="button"
            onClick={() => {
              setSelectedThreadId(null);
              setWantsNewThread(true);
              setShowThreadList(false);
              setError(null);
            }}
            className="rounded border border-[var(--line)] px-2 py-0.5 text-[var(--text-muted)] hover:border-[var(--accent)]/40 hover:text-[var(--text-primary)]"
          >
            + new thread
          </button>
        </div>

        {showThreadList && (
          <ul className="max-h-40 overflow-y-auto border-b border-[var(--line)] bg-[var(--surface-1)] py-1 text-xs">
            {(threadsQuery.data ?? []).map((t) => (
              <li key={t.id}>
                <button
                  type="button"
                  onClick={() => {
                    setSelectedThreadId(t.id);
                    setWantsNewThread(false);
                    setShowThreadList(false);
                  }}
                  className={`block w-full truncate px-4 py-1.5 text-left transition hover:bg-[var(--surface-2)] ${
                    t.id === activeThreadId ? "text-[var(--accent)]" : ""
                  }`}
                  title={t.title ?? "(untitled)"}
                >
                  {t.title ?? "(untitled)"}
                </button>
              </li>
            ))}
            {(threadsQuery.data ?? []).length === 0 && (
              <li className="px-4 py-2 text-[var(--text-muted)]">No past threads.</li>
            )}
          </ul>
        )}

        {/* Messages */}
        <div
          ref={scrollerRef}
          className="flex-1 space-y-3 overflow-y-auto px-4 py-3 text-sm"
        >
          {activeThreadId === null && rendered.length === 0 && (
            <div className="rounded border border-dashed border-[var(--line)] p-4 text-center text-xs text-[var(--text-muted)]">
              Start a new conversation with {displayName} — they can speak to
              their own recent work, learnings, and project context.
            </div>
          )}
          {rendered.map((m) => (
            <MessageBubble key={m.id} message={m} agentDisplayName={displayName} />
          ))}
        </div>

        {/* Input */}
        <form
          className="border-t border-[var(--line)] p-3"
          onSubmit={(e) => {
            e.preventDefault();
            onSend();
          }}
        >
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                e.preventDefault();
                onSend();
              }
            }}
            placeholder={`Ask ${displayName} something…`}
            rows={2}
            className="block w-full resize-none rounded border border-[var(--line)] bg-[var(--surface-1)] px-2 py-1.5 text-sm outline-none focus:border-[var(--accent)]/60"
            disabled={sendMutation.isPending}
          />
          <div className="mt-2 flex items-center justify-between gap-2 text-[10px] text-[var(--text-muted)]">
            <span>⌘/Ctrl + Enter to send</span>
            <button
              type="submit"
              disabled={!draft.trim() || sendMutation.isPending}
              className="rounded border border-[var(--accent)]/40 px-3 py-1 text-xs text-[var(--accent)] transition hover:bg-[var(--accent)]/10 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {sendMutation.isPending ? "sending…" : "send"}
            </button>
          </div>
          {error && (
            <div className="mt-2 rounded border border-rose-400/40 bg-rose-400/10 px-2 py-1 text-[11px] text-rose-200">
              {error}
            </div>
          )}
        </form>

        {/* Footer */}
        <div className="border-t border-[var(--line)] bg-[var(--surface-1)] px-4 py-1.5 text-center text-[10px] text-[var(--text-muted)]">
          agent persona · simulated from learning store + recent work
          {lastModel ? ` · model: ${lastModel}` : ""}
        </div>
      </aside>
    </>
  );
}

function MessageBubble({
  message,
  agentDisplayName,
}: {
  message: MessageRow;
  agentDisplayName: string;
}) {
  const isUser = message.role === "user";
  const isThinking = message.id === "optimistic-agent";
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[85%] rounded-lg px-3 py-2 text-sm ${
          isUser
            ? "bg-[var(--accent)]/15 text-[var(--text-primary)]"
            : "bg-[var(--surface-1)] text-[var(--text-primary)]"
        } ${isThinking ? "animate-pulse italic text-[var(--text-muted)]" : ""}`}
      >
        {!isUser && (
          <div className="mb-1 text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
            {agentDisplayName}
          </div>
        )}
        <div className="whitespace-pre-wrap break-words">{message.content}</div>
      </div>
    </div>
  );
}
