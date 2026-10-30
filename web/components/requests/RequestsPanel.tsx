"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

const MAX_TEXT = 4000; // keep in step with /api/requests and migration 0022

type ReqQuestion = { id: string; status: string; question: string; answer: string | null };
type WorkRequest = {
  id: string;
  project: string;
  text: string;
  status: "queued" | "clarifying" | "filed" | "refused" | "failed";
  result: {
    reason?: string;
    issues?: number[];
    decisions?: string[];
    assumptions?: string[];
  } | null;
  created_at: string;
  questions: ReqQuestion[];
};

async function fetchRequests(): Promise<{ requests: WorkRequest[] }> {
  const r = await fetch("/api/requests", { cache: "no-store" });
  if (!r.ok) throw new Error("could not load requests");
  return r.json();
}

async function post(url: string, body: unknown): Promise<unknown> {
  const r = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  const j = (await r.json().catch(() => ({}))) as { error?: string };
  if (!r.ok) throw new Error(j.error ?? `request failed (${r.status})`);
  return j;
}

const STATUS_LABEL: Record<WorkRequest["status"], string> = {
  queued: "waiting for the crew",
  clarifying: "needs your answer",
  filed: "scoped",
  refused: "not taken",
  failed: "failed",
};

export function RequestsPanel({ projects }: { projects: string[] }) {
  const qc = useQueryClient();
  const [project, setProject] = useState(projects[0] ?? "");
  const [text, setText] = useState("");
  const list = useQuery({
    queryKey: ["work-requests"],
    queryFn: fetchRequests,
    // Poll while the crew may still be working; the cron drains the queue.
    refetchInterval: (q) =>
      q.state.data?.requests.some((r) => r.status === "queued" || r.status === "clarifying")
        ? 5000
        : 30000,
  });
  const submit = useMutation({
    mutationFn: () => post("/api/requests", { project, text }),
    onSuccess: () => {
      setText("");
      void qc.invalidateQueries({ queryKey: ["work-requests"] });
    },
  });

  const tooLong = text.length > MAX_TEXT;
  const canSubmit = !!project && text.trim().length > 0 && !tooLong && !submit.isPending;

  return (
    <div className="flex flex-col gap-8">
      <form
        className="flex flex-col gap-3 rounded-xl border border-[var(--line)] bg-[var(--bg-elevated)] p-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (canSubmit) submit.mutate();
        }}
      >
        <label className="flex flex-col gap-1 text-xs uppercase tracking-wider text-[var(--text-muted)]">
          Project
          <select
            value={project}
            onChange={(e) => setProject(e.target.value)}
            className="rounded border border-[var(--line)] bg-[var(--bg-surface)] px-2 py-1.5 text-sm normal-case tracking-normal text-[var(--text-primary)]"
          >
            {projects.length === 0 && <option value="">no projects yet</option>}
            {projects.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs uppercase tracking-wider text-[var(--text-muted)]">
          What do you want?
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={4}
            placeholder="e.g. add a famous persona to the personas project"
            className="rounded border border-[var(--line)] bg-[var(--bg-surface)] px-3 py-2 text-sm normal-case tracking-normal text-[var(--text-primary)]"
          />
        </label>
        <div className="flex items-center justify-between">
          <span className={`text-xs ${tooLong ? "text-red-400" : "text-[var(--text-muted)]"}`}>
            {text.length}/{MAX_TEXT}
          </span>
          <button
            type="submit"
            disabled={!canSubmit}
            className="rounded-md border border-[var(--accent)] bg-[var(--accent)]/10 px-3 py-1.5 text-xs font-medium uppercase tracking-wider text-[var(--text-primary)] disabled:cursor-not-allowed disabled:opacity-40"
          >
            {submit.isPending ? "sending…" : "send to the crew"}
          </button>
        </div>
        {submit.isError && (
          <p role="alert" className="text-xs text-red-400">
            {(submit.error as Error).message}
          </p>
        )}
      </form>

      <section aria-label="Your requests" className="flex flex-col gap-3">
        <h2 className="text-xs uppercase tracking-wider text-[var(--text-muted)]">Your requests</h2>
        {list.isError && <p className="text-xs text-red-400">Could not load your requests.</p>}
        {list.data?.requests.length === 0 && (
          <p className="text-sm text-[var(--text-muted)]">Nothing yet.</p>
        )}
        {list.data?.requests.map((r) => <RequestCard key={r.id} req={r} />)}
      </section>
    </div>
  );
}

function RequestCard({ req }: { req: WorkRequest }) {
  const res = req.result ?? {};
  return (
    <article className="rounded-xl border border-[var(--line)] bg-[var(--bg-elevated)] p-4">
      <div className="mb-1 flex items-center justify-between gap-3">
        <span className="font-mono text-xs text-[var(--accent)]">{req.project}</span>
        <span className="rounded border border-[var(--line)] px-2 py-0.5 text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
          {STATUS_LABEL[req.status]}
        </span>
      </div>
      {/* Request text is user input: rendered as text, never as HTML. */}
      <p className="whitespace-pre-wrap text-sm">{req.text}</p>

      {req.status === "clarifying" && (
        <div className="mt-3 flex flex-col gap-3">
          {req.questions.map((q) => (
            <QuestionForm key={q.id} requestId={req.id} q={q} />
          ))}
        </div>
      )}
      {req.status === "queued" && (
        <p className="mt-2 text-xs text-[var(--text-muted)]">
          Queued. The crew picks requests up every few hours.
        </p>
      )}
      {req.status === "filed" && (
        <p className="mt-2 text-xs text-[var(--text-muted)]">
          {res.issues?.length ? `Filed ${res.issues.length} issue(s). ` : ""}
          {res.decisions?.length
            ? `${res.decisions.length} item(s) wait for your approval in the decisions queue.`
            : "Approved work is queued for the crew."}
        </p>
      )}
      {(req.status === "refused" || req.status === "failed") && res.reason && (
        <p className="mt-2 text-xs text-[var(--text-muted)]">{res.reason}</p>
      )}
    </article>
  );
}

function QuestionForm({ requestId, q }: { requestId: string; q: ReqQuestion }) {
  const qc = useQueryClient();
  const [answer, setAnswer] = useState("");
  const send = useMutation({
    mutationFn: () => post("/api/requests/answer", { request_id: requestId, question_id: q.id, answer }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["work-requests"] }),
  });
  if (q.status === "answered") {
    return (
      <div className="text-xs text-[var(--text-muted)]">
        <p>{q.question}</p>
        <p className="mt-1 text-[var(--text-primary)]">↳ {q.answer}</p>
      </div>
    );
  }
  return (
    <form
      className="flex flex-col gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (answer.trim()) send.mutate();
      }}
    >
      <p className="text-sm">{q.question}</p>
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
      {send.isError && <p role="alert" className="text-xs text-red-400">{(send.error as Error).message}</p>}
    </form>
  );
}
