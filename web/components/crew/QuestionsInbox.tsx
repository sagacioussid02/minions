"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type Question } from "@/lib/schemas";

async function fetchQuestions(): Promise<{ questions: Question[] }> {
  const r = await fetch("/api/questions", { cache: "no-store" });
  if (!r.ok) throw new Error("could not load questions");
  return r.json();
}

/** Questions the crew is blocked on, answerable in place. Hidden when there are none. */
export function QuestionsInbox() {
  const q = useQuery({ queryKey: ["questions"], queryFn: fetchQuestions, refetchInterval: 15000 });
  const open = q.data?.questions ?? [];
  if (q.isError) {
    return <p className="text-xs text-red-400">Could not load the crew&apos;s questions.</p>;
  }
  if (open.length === 0) return null;
  return (
    <section
      aria-label="Questions for you"
      className="rounded-xl border border-[var(--accent)]/50 bg-[var(--accent)]/5 p-4"
    >
      <h2 className="mb-3 flex items-center gap-2 text-xs uppercase tracking-wider text-[var(--text-muted)]">
        Questions for you
        <span className="rounded bg-[var(--bg-surface)] px-1.5 font-mono text-[10px]">
          {open.length}
        </span>
      </h2>
      <ul className="flex flex-col gap-4">
        {open.map((item) => (
          <li key={item.id}>
            <AnswerForm q={item} />
          </li>
        ))}
      </ul>
    </section>
  );
}

function AnswerForm({ q }: { q: Question }) {
  const qc = useQueryClient();
  const [answer, setAnswer] = useState("");
  const send = useMutation({
    mutationFn: async () => {
      const r = await fetch("/api/questions/answer", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ question_id: q.id, answer }),
      });
      const j = (await r.json().catch(() => ({}))) as { error?: string };
      if (!r.ok) throw new Error(j.error ?? `failed (${r.status})`);
    },
    onSuccess: () => {
      setAnswer("");
      void qc.invalidateQueries({ queryKey: ["questions"] });
      void qc.invalidateQueries({ queryKey: ["work-requests"] });
    },
  });
  return (
    <form
      className="flex flex-col gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (answer.trim()) send.mutate();
      }}
    >
      <div className="text-xs text-[var(--text-muted)]">
        {q.project} · {q.asker_role.replace(/_/g, " ")}
      </div>
      {/* Question text can originate from the crew/LLM: rendered as text only. */}
      <p className="whitespace-pre-wrap text-sm">{q.question}</p>
      <div className="flex gap-2">
        <input
          value={answer}
          onChange={(e) => setAnswer(e.target.value)}
          maxLength={2000}
          aria-label={`Answer: ${q.question}`}
          className="flex-1 rounded border border-[var(--line)] bg-[var(--bg-surface)] px-2 py-1.5 text-sm"
        />
        <button
          type="submit"
          disabled={!answer.trim() || send.isPending}
          className="rounded-md border border-[var(--accent)] bg-[var(--accent)]/10 px-3 text-xs uppercase tracking-wider disabled:opacity-40"
        >
          {send.isPending ? "…" : "answer"}
        </button>
      </div>
      {send.isError && (
        <p role="alert" className="text-xs text-red-400">
          {(send.error as Error).message}
        </p>
      )}
    </form>
  );
}
