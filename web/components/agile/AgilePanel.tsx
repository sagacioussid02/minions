"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { type AgilePanel as Panel, type PMAnswer } from "@/lib/schemas";

async function fetchAgile(): Promise<Panel> {
  const r = await fetch("/api/agile", { cache: "no-store" });
  if (!r.ok) throw new Error("agile fetch failed");
  return r.json();
}

export function AgilePanel({ initial }: { initial: Panel }) {
  const [project, setProject] = useState(
    initial.projects[0] ?? initial.artifacts[0]?.project ?? "",
  );
  const [question, setQuestion] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { data } = useQuery({
    queryKey: ["agile-panel"],
    queryFn: fetchAgile,
    initialData: initial,
    refetchInterval: 10_000,
  });
  const latest = data.artifacts.slice(0, 8);
  const answers = data.pm_answers.slice(0, 3);
  const projects = useMemo(
    () =>
      Array.from(
        new Set([...data.projects, ...data.artifacts.map((a) => a.project)]),
      ).sort(),
    [data.artifacts, data.projects],
  );

  const selectedProject = project || projects[0] || "";

  async function submitQuestion() {
    if (!selectedProject || !question.trim()) return;
    setPending(true);
    setError(null);
    try {
      const response = await fetch("/api/pm-answer", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project: selectedProject, question }),
      });
      if (!response.ok) throw new Error(`PM answer failed (${response.status})`);
      const answer = (await response.json()) as PMAnswer;
      data.pm_answers.unshift(answer);
      setQuestion("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "PM answer failed");
    } finally {
      setPending(false);
    }
  }

  return (
    <section className="rounded-xl border border-[var(--line)] bg-[var(--bg-surface)] p-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div>
          <h2 className="text-sm font-medium text-[var(--text-primary)]">Agile cadence</h2>
          <p className="text-xs text-[var(--text-muted)]">
            Scrum, sprint planning, monthly demo, and PM answers.
          </p>
        </div>
        <span className="rounded bg-[var(--bg-elevated)] px-2 py-1 text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
          {latest.length} artifacts
        </span>
      </div>
      <div className="grid gap-3 lg:grid-cols-2">
        <div className="grid gap-2">
          {latest.length === 0 ? (
            <div className="rounded-lg border border-dashed border-[var(--line)] p-3 text-xs text-[var(--text-muted)]">
              No Agile rituals recorded yet. Run `minions cron scrum`.
            </div>
          ) : (
            latest.map((item) => (
              <article key={item.id} className="rounded-lg border border-[var(--line)] bg-[var(--bg-elevated)] p-3">
                <div className="mb-1 flex items-center gap-2 text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
                  <span>{item.project}</span>
                  <span>·</span>
                  <span>{label(item.ritual)}</span>
                  {item.blockers.length > 0 && (
                    <span className="ml-auto rounded bg-[var(--state-warn)]/15 px-1.5 text-[var(--state-warn)]">
                      {item.blockers.length} blocker
                    </span>
                  )}
                </div>
                <p className="text-xs text-[var(--text-primary)]">{item.summary}</p>
                {item.next_actions[0] && (
                  <p className="mt-1 text-[10px] text-[var(--text-muted)]">
                    Next: {item.next_actions[0]}
                  </p>
                )}
              </article>
            ))
          )}
        </div>
        <div className="grid content-start gap-2">
          <div className="rounded-lg border border-[var(--line)] bg-[var(--bg-elevated)] p-3">
            <div className="mb-2 text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
              Ask Product Manager
            </div>
            <div className="grid gap-2">
              <select
                value={selectedProject}
                onChange={(event) => setProject(event.target.value)}
                className="rounded-md border border-[var(--line)] bg-[var(--bg-surface)] px-2 py-1 text-xs text-[var(--text-primary)]"
              >
                <option value="" disabled>
                  Project
                </option>
                {projects.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
              <input
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                placeholder="Ask about status, stack, or key rotation"
                className="rounded-md border border-[var(--line)] bg-[var(--bg-surface)] px-2 py-1 text-xs text-[var(--text-primary)]"
              />
              <button
                type="button"
                disabled={pending || !selectedProject || !question.trim()}
                onClick={submitQuestion}
                className="rounded-md bg-[var(--accent)]/15 px-2 py-1 text-[10px] font-medium uppercase tracking-wider text-[var(--accent)] hover:bg-[var(--accent)]/25 disabled:opacity-40"
              >
                {pending ? "Answering" : "Ask PM"}
              </button>
              {error && <p className="text-[10px] text-[var(--state-danger)]">{error}</p>}
            </div>
          </div>
          {answers.length === 0 ? (
            <div className="rounded-lg border border-dashed border-[var(--line)] p-3 text-xs text-[var(--text-muted)]">
              No PM answers yet. Use `minions ask-pm &lt;project&gt; &lt;question&gt;`.
            </div>
          ) : (
            answers.map((answer) => (
              <article key={answer.id} className="rounded-lg border border-[var(--line)] bg-[var(--bg-elevated)] p-3">
                <div className="mb-1 text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
                  {answer.project} · Product Manager
                </div>
                <p className="text-xs font-medium text-[var(--text-primary)]">{answer.question}</p>
                <p className="mt-1 line-clamp-3 text-xs text-[var(--text-muted)]">{answer.answer}</p>
                {answer.escalated_to && (
                  <p className="mt-1 text-[10px] text-[var(--state-warn)]">
                    Escalated to {answer.escalated_to}
                  </p>
                )}
              </article>
            ))
          )}
        </div>
      </div>
    </section>
  );
}

function label(ritual: string): string {
  return ritual.replaceAll("_", " ");
}
