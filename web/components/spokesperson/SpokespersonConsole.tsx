"use client";

import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type {
  Consultation,
  InterviewBundle,
  InterviewTaskProposal,
  InterviewThread,
  SpokespersonAnswer,
} from "@/lib/schemas";

type InitialData = {
  roles: string[];
  projects: string[];
  threads: InterviewThread[];
};

const EXECUTIVE_COPY: Record<string, { title: string; remit: string; prompts: string[] }> = {
  ceo: {
    title: "CEO",
    remit: "Strategy, priorities, investor narrative",
    prompts: [
      "What should we focus on before the investor demo?",
      "Which project deserves more capacity this week?",
    ],
  },
  cto: {
    title: "CTO",
    remit: "Architecture, scaling, technical risk",
    prompts: [
      "Can sidspace scale if we onboard 100 customers?",
      "Where is sidspace UI deployed right now?",
    ],
  },
  chief_product_officer: {
    title: "Product",
    remit: "Roadmap, users, scope, demo story",
    prompts: [
      "What changed this sprint that matters to customers?",
      "What should sidspace demo next?",
    ],
  },
  coo: {
    title: "Delivery",
    remit: "Execution, blockers, cross-team flow",
    prompts: [
      "What is blocked across the company today?",
      "Which approved work is not moving?",
    ],
  },
  managing_director: {
    title: "Finance",
    remit: "Spend, budget, portfolio tradeoffs",
    prompts: [
      "What costs are trending up?",
      "Where should we spend more aggressively?",
    ],
  },
  security_champion: {
    title: "Security",
    remit: "Secrets, risk, compliance posture",
    prompts: [
      "How do we rotate API keys safely?",
      "Which project has the highest security risk?",
    ],
  },
  portfolio_owner: {
    title: "Portfolio",
    remit: "Cross-project ownership and allocation",
    prompts: [
      "List all repos owned by this minions org.",
      "Which projects are active vs deferred?",
    ],
  },
  product_manager: {
    title: "Product Manager",
    remit: "Project status, scope, sprint detail",
    prompts: [
      "What is the current sprint goal for sidspace?",
      "What did the team finish recently?",
    ],
  },
  spokesperson: {
    title: "Chief of Staff",
    remit: "Front door for any company question",
    prompts: [
      "What needs my attention today?",
      "Summarize the company in plain English.",
    ],
  },
};

async function fetchThreads(project: string | null): Promise<{ threads: InterviewThread[] }> {
  const params = new URLSearchParams();
  if (project) params.set("project", project);
  const r = await fetch(`/api/spokesperson/threads?${params.toString()}`, {
    cache: "no-store",
  });
  if (!r.ok) throw new Error("thread fetch failed");
  return r.json();
}

async function fetchBundle(threadId: string | null): Promise<InterviewBundle | null> {
  if (!threadId) return null;
  const r = await fetch(`/api/spokesperson/threads/${threadId}`, { cache: "no-store" });
  if (!r.ok) throw new Error("thread detail fetch failed");
  return r.json();
}

export function SpokespersonConsole({ initial }: { initial: InitialData }) {
  const qc = useQueryClient();
  const defaultRole = initial.roles.includes("cto") ? "cto" : initial.roles[0] ?? "cto";
  const [role, setRole] = useState(defaultRole);
  const [project, setProject] = useState<string | null>(null);
  const [threadId, setThreadId] = useState<string | null>(initial.threads[0]?.id ?? null);
  const [question, setQuestion] = useState("");
  const [showEvidence, setShowEvidence] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const threadsQuery = useQuery({
    queryKey: ["spokesperson-threads", project],
    queryFn: () => fetchThreads(project),
    initialData: { threads: initial.threads },
    refetchInterval: 5_000,
  });
  const bundleQuery = useQuery({
    queryKey: ["spokesperson-thread", threadId],
    queryFn: () => fetchBundle(threadId),
    enabled: Boolean(threadId),
    refetchInterval: threadId ? 4_000 : false,
  });

  const threads = threadsQuery.data.threads;
  const bundle = bundleQuery.data;
  const selectedThread = bundle?.thread ?? threads.find((t) => t.id === threadId) ?? null;
  const latestAnswer = [...(bundle?.messages ?? [])].reverse().find((m) => m.role === "spokesperson");
  const latestCitations = latestAnswer?.citations ?? [];
  const followUpActions = latestAnswer?.follow_up_actions ?? [];
  const tasks = bundle?.tasks ?? [];
  const activeExec = executiveFor(role);

  const projectOptions = useMemo(
    () => Array.from(new Set(initial.projects)).sort(),
    [initial.projects],
  );

  const executiveRoles = useMemo(() => {
    const ordered = [
      "ceo",
      "cto",
      "chief_product_officer",
      "coo",
      "managing_director",
      "security_champion",
      "portfolio_owner",
      "product_manager",
    ];
    return ordered.filter((item) => initial.roles.includes(item));
  }, [initial.roles]);

  async function submitQuestion(promptOverride?: string) {
    const text = (promptOverride ?? question).trim();
    if (!text) return;
    setPending(true);
    setError(null);
    try {
      let activeThreadId = threadId;
      if (!activeThreadId) {
        const threadResponse = await fetch("/api/spokesperson/threads", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            spokesperson_role: role,
            project,
            title: text,
          }),
        });
        if (!threadResponse.ok) {
          throw new Error(`thread create failed (${threadResponse.status})`);
        }
        const thread = (await threadResponse.json()) as InterviewThread;
        activeThreadId = thread.id;
        setThreadId(thread.id);
      }
      const response = await fetch(`/api/spokesperson/threads/${activeThreadId}/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: text, project, spokesperson_role: role }),
      });
      if (!response.ok) throw new Error(`leadership answer failed (${response.status})`);
      const answer = (await response.json()) as SpokespersonAnswer;
      setQuestion("");
      setThreadId(answer.thread.id);
      setProject(answer.thread.project);
      setShowEvidence(false);
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["spokesperson-threads"] }),
        qc.invalidateQueries({ queryKey: ["spokesperson-thread", answer.thread.id] }),
      ]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "leadership answer failed");
    } finally {
      setPending(false);
    }
  }

  async function createTask(task: InterviewTaskProposal) {
    if (!threadId || !task.title.trim()) return;
    setPending(true);
    setError(null);
    try {
      const response = await fetch(`/api/spokesperson/threads/${threadId}/tasks`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message_id: task.message_id,
          owner_role: task.owner_role,
          title: task.title,
          rationale: task.rationale,
        }),
      });
      if (!response.ok) throw new Error(`delegation failed (${response.status})`);
      await qc.invalidateQueries({ queryKey: ["spokesperson-thread", threadId] });
    } catch (err) {
      setError(err instanceof Error ? err.message : "delegation failed");
    } finally {
      setPending(false);
    }
  }

  return (
    <section className="mx-auto grid min-h-[calc(100vh-6.5rem)] w-full max-w-[1420px] gap-4 xl:grid-cols-[320px_minmax(0,1fr)]">
      <aside className="flex min-h-0 flex-col gap-3">
        <section className="rounded-lg border border-[var(--line)] bg-[var(--bg-surface)] p-3">
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="text-[10px] font-medium uppercase tracking-wider text-[var(--accent)]">
                Leadership Room
              </p>
              <h2 className="mt-1 text-lg font-semibold text-[var(--text-primary)]">
                Talk to the team that runs the company.
              </h2>
            </div>
            <button
              type="button"
              onClick={() => {
                setThreadId(null);
                setQuestion("");
                setShowEvidence(false);
              }}
              className="rounded-md border border-[var(--line)] px-2.5 py-1 text-[10px] uppercase tracking-wider text-[var(--text-muted)] hover:border-[var(--accent)]/50"
            >
              New
            </button>
          </div>
          <p className="mt-2 text-xs leading-5 text-[var(--text-muted)]">
            Ask strategy, scaling, product, delivery, security, or granular project questions. Executives answer directly or delegate the work internally.
          </p>
        </section>

        <section className="rounded-lg border border-[var(--line)] bg-[var(--bg-surface)] p-3">
          <div className="mb-2 text-[10px] font-medium uppercase tracking-wider text-[var(--text-muted)]">
            Choose an executive
          </div>
          <div className="grid gap-2">
            {executiveRoles.map((item) => {
              const meta = executiveFor(item);
              const active = role === item;
              return (
                <button
                  key={item}
                  type="button"
                  onClick={() => setRole(item)}
                  className={`rounded-lg border p-3 text-left transition ${
                    active
                      ? "border-[var(--accent)]/60 bg-[var(--accent)]/10"
                      : "border-[var(--line)] bg-[var(--bg-elevated)] hover:border-[var(--accent)]/40"
                  }`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-sm font-semibold text-[var(--text-primary)]">{meta.title}</span>
                    {active && (
                      <span className="rounded-full bg-[var(--accent)]/15 px-2 py-0.5 text-[9px] uppercase tracking-wider text-[var(--accent)]">
                        in room
                      </span>
                    )}
                  </div>
                  <div className="mt-1 text-xs leading-4 text-[var(--text-muted)]">{meta.remit}</div>
                </button>
              );
            })}
          </div>
        </section>

        <section className="min-h-0 flex-1 rounded-lg border border-[var(--line)] bg-[var(--bg-surface)] p-3">
          <div className="mb-2 flex items-center justify-between gap-2">
            <h2 className="text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)]">
              Recent conversations
            </h2>
            <select
              value={project ?? "org"}
              onChange={(event) => {
                const value = event.target.value === "org" ? null : event.target.value;
                setProject(value);
                setThreadId(null);
              }}
              className="max-w-36 rounded-md border border-[var(--line)] bg-[var(--bg-elevated)] px-2 py-1 text-xs text-[var(--text-primary)]"
              aria-label="Conversation scope"
            >
              <option value="org">Company</option>
              {projectOptions.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
          </div>
          <div className="grid max-h-[22rem] gap-1.5 overflow-y-auto pr-1">
            {threads.length === 0 ? (
              <div className="rounded-lg border border-dashed border-[var(--line)] p-3 text-xs leading-5 text-[var(--text-muted)]">
                No conversations yet. Start with a leadership question.
              </div>
            ) : (
              threads.map((thread) => (
                <button
                  key={thread.id}
                  type="button"
                  onClick={() => {
                    setThreadId(thread.id);
                    setShowEvidence(false);
                  }}
                  className={`rounded-md border p-2.5 text-left transition ${
                    thread.id === threadId
                      ? "border-[var(--accent)]/60 bg-[var(--accent)]/10"
                      : "border-[var(--line)] bg-[var(--bg-elevated)] hover:border-[var(--accent)]/40"
                  }`}
                >
                  <div className="line-clamp-2 text-xs font-medium text-[var(--text-primary)]">
                    {thread.title}
                  </div>
                  <div className="mt-1 text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
                    {executiveFor(thread.spokesperson_role).title} · {thread.project ?? "company"}
                  </div>
                </button>
              ))
            )}
          </div>
        </section>
      </aside>

      <main className="min-w-0 rounded-lg border border-[var(--line)] bg-[var(--bg-surface)]">
        <header className="border-b border-[var(--line)] p-4">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <span className="rounded-full border border-[var(--line)] bg-[var(--bg-elevated)] px-2.5 py-1 text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
                  {activeExec.title}
                </span>
                <span className="text-xs text-[var(--text-muted)]">
                  {selectedThread?.project ?? project ?? "Company-wide"}
                </span>
              </div>
              <h1 className="mt-2 text-xl font-semibold leading-tight text-[var(--text-primary)]">
                {selectedThread?.title ?? "What should leadership think through next?"}
              </h1>
              <p className="mt-1 max-w-3xl text-sm leading-5 text-[var(--text-muted)]">
                {activeExec.remit}. If the question needs deeper work, this executive will route it to the right crew and keep the answer tied to this room.
              </p>
            </div>
            <ConfidencePill confidence={latestAnswer?.confidence ?? "unknown"} />
          </div>
        </header>

        <div className="grid gap-4 p-4 xl:grid-cols-[minmax(0,1fr)_320px]">
          <section className="min-w-0">
            <div className="grid max-h-[46vh] min-h-[18rem] gap-3 overflow-y-auto pr-1">
              {(bundle?.messages ?? []).length === 0 ? (
                <EmptyConversation
                  exec={activeExec}
                  pending={pending}
                  onPrompt={(prompt) => submitQuestion(prompt)}
                />
              ) : (
                bundle?.messages.map((message) => (
                  <article
                    key={message.id}
                    className={`rounded-lg border p-3 ${
                      message.role === "operator"
                        ? "ml-auto max-w-[78%] border-[var(--accent)]/30 bg-[var(--accent)]/10"
                        : "mr-auto max-w-[88%] border-[var(--line)] bg-[var(--bg-elevated)]"
                    }`}
                  >
                    <div className="mb-1 text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
                      {message.role === "operator" ? "Owner" : executiveFor(message.agent_role ?? "spokesperson").title}
                    </div>
                    <p className="whitespace-pre-wrap text-sm leading-6 text-[var(--text-primary)]">
                      {leadershipLanguage(message.content)}
                    </p>
                  </article>
                ))
              )}
            </div>

            <div className="mt-4 grid gap-2">
              <textarea
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                placeholder={`Ask ${activeExec.title}: ${activeExec.prompts[0]}`}
                className="min-h-24 resize-none rounded-lg border border-[var(--line)] bg-[var(--bg-elevated)] px-3 py-2 text-sm leading-6 text-[var(--text-primary)] outline-none focus:border-[var(--accent)]/50"
              />
              <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                {error ? (
                  <p className="text-xs text-[var(--state-danger)]">{error}</p>
                ) : (
                  <p className="text-xs text-[var(--text-muted)]">
                    Project scope is inferred from your question when you name one.
                  </p>
                )}
                <button
                  type="button"
                  disabled={pending || !question.trim()}
                  onClick={() => submitQuestion()}
                  className="rounded-md bg-[var(--accent)] px-4 py-2 text-xs font-semibold uppercase tracking-wider text-white shadow-sm hover:brightness-105 disabled:opacity-40"
                >
                  {pending ? "Working" : `Ask ${activeExec.title}`}
                </button>
              </div>
            </div>
          </section>

          <aside className="grid content-start gap-3">
            <DelegationTimeline consultations={bundle?.consultations ?? []} tasks={tasks} />
            <Panel title="Leadership commitments">
              <FollowUps
                actions={followUpActions}
                tasks={tasks}
                pending={pending}
                latestAnswer={latestAnswer}
                role={role}
                project={project}
                threadId={threadId}
                onCreate={createTask}
              />
            </Panel>
            <section className="rounded-lg border border-[var(--line)] bg-[var(--bg-elevated)] p-3">
              <button
                type="button"
                onClick={() => setShowEvidence((value) => !value)}
                className="flex w-full items-center justify-between gap-3 text-left"
              >
                <span>
                  <span className="block text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)]">
                    Evidence
                  </span>
                  <span className="mt-1 block text-xs text-[var(--text-muted)]">
                    {latestCitations.length} source{latestCitations.length === 1 ? "" : "s"} available
                  </span>
                </span>
                <span className="text-xs text-[var(--accent)]">{showEvidence ? "Hide" : "Open"}</span>
              </button>
              {showEvidence && <EvidenceList citations={latestCitations} />}
            </section>
          </aside>
        </div>
      </main>
    </section>
  );
}

function EmptyConversation({
  exec,
  pending,
  onPrompt,
}: {
  exec: { title: string; prompts: string[] };
  pending: boolean;
  onPrompt: (prompt: string) => void;
}) {
  return (
    <div className="rounded-lg border border-dashed border-[var(--line)] bg-[var(--bg-elevated)]/60 p-5">
      <h2 className="text-base font-semibold text-[var(--text-primary)]">
        Start with the kind of question you would ask a real executive.
      </h2>
      <p className="mt-2 max-w-2xl text-sm leading-6 text-[var(--text-muted)]">
        High-level strategy, granular deployment details, sprint risk, staffing, repo inventory, and security questions all begin here. Leadership answers, delegates, and brings the response back.
      </p>
      <div className="mt-4 grid gap-2 md:grid-cols-2">
        {exec.prompts.map((prompt) => (
          <button
            key={prompt}
            type="button"
            disabled={pending}
            onClick={() => onPrompt(prompt)}
            className="rounded-lg border border-[var(--line)] bg-[var(--bg-surface)] p-3 text-left text-sm leading-5 text-[var(--text-primary)] hover:border-[var(--accent)]/40 disabled:opacity-40"
          >
            {prompt}
          </button>
        ))}
      </div>
    </div>
  );
}

function DelegationTimeline({
  consultations,
  tasks,
}: {
  consultations: Consultation[];
  tasks: InterviewTaskProposal[];
}) {
  return (
    <Panel title="Delegation">
      {consultations.length === 0 && tasks.length === 0 ? (
        <p className="text-xs leading-5 text-[var(--text-muted)]">
          No delegation yet. Leadership will route granular work here when a deeper answer is needed.
        </p>
      ) : (
        <div className="grid gap-2">
          {consultations.map((consultation) => (
            <div key={consultation.id} className="flex gap-2 rounded-lg border border-[var(--line)] bg-[var(--bg-elevated)] p-2.5">
              <StatusDot status={consultation.status} />
              <div className="min-w-0">
                <div className="text-xs font-medium text-[var(--text-primary)]">
                  {roleTitle(consultation.consulted_role)}
                </div>
                <div className="mt-0.5 text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
                  {delegationStatus(consultation.status)} · {consultation.confidence}
                </div>
                {consultation.files_inspected.length > 0 && (
                  <div className="mt-1 truncate text-[10px] text-[var(--text-muted)]">
                    Checked {consultation.files_inspected.slice(0, 3).join(", ")}
                  </div>
                )}
              </div>
            </div>
          ))}
          {tasks.map((task) => (
            <div key={task.id} className="rounded-lg border border-[var(--state-warn)]/35 bg-[var(--state-warn)]/10 p-2.5">
              <div className="text-[10px] uppercase tracking-wider text-[var(--state-warn)]">
                Sprint Board SPIKE · {roleTitle(task.owner_role)}
              </div>
              <p className="mt-1 text-xs font-medium text-[var(--text-primary)]">{task.title}</p>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

function FollowUps({
  actions,
  tasks,
  pending,
  latestAnswer,
  role,
  project,
  threadId,
  onCreate,
}: {
  actions: string[];
  tasks: InterviewTaskProposal[];
  pending: boolean;
  latestAnswer: InterviewBundle["messages"][number] | undefined;
  role: string;
  project: string | null;
  threadId: string | null;
  onCreate: (task: InterviewTaskProposal) => void;
}) {
  if (actions.length === 0 && tasks.length === 0) {
    return (
      <p className="text-xs leading-5 text-[var(--text-muted)]">
        Open questions, promised investigations, and owner-visible next steps appear here.
      </p>
    );
  }
  return (
    <div className="grid gap-2">
      {actions.map((action) => (
        <button
          key={action}
          type="button"
          disabled={pending || !latestAnswer}
          onClick={() =>
            onCreate({
              id: "pending",
              thread_id: threadId ?? "",
              message_id: latestAnswer?.id ?? "",
              project,
              owner_role: latestAnswer?.consulted_roles[1] ?? role,
              title: action,
              rationale: `Created from leadership answer ${latestAnswer?.id ?? ""}`,
              status: "pending",
              decision_id: null,
              created_at: new Date().toISOString(),
            })
          }
          className="rounded-lg border border-[var(--state-warn)]/35 bg-[var(--state-warn)]/10 p-2 text-left text-xs leading-5 text-[var(--text-primary)] hover:bg-[var(--state-warn)]/15 disabled:opacity-40"
        >
          {action}
        </button>
      ))}
      {tasks.map((task) => (
        <div key={task.id} className="rounded-lg border border-[var(--line)] bg-[var(--bg-elevated)] p-2">
          <div className="text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
            {roleTitle(task.owner_role)} · {task.status}
          </div>
          <p className="mt-1 text-xs font-medium text-[var(--text-primary)]">{task.title}</p>
        </div>
      ))}
    </div>
  );
}

function EvidenceList({ citations }: { citations: NonNullable<SpokespersonAnswer["answer_message"]["citations"]> }) {
  if (citations.length === 0) {
    return <p className="mt-3 text-xs text-[var(--text-muted)]">No citations recorded yet.</p>;
  }
  return (
    <div className="mt-3 grid max-h-[18rem] gap-2 overflow-y-auto pr-1">
      {citations.map((citation, idx) => (
        <div key={`${citation.label}-${idx}`} className="rounded-lg border border-[var(--line)] bg-[var(--bg-surface)] p-2">
          <div className="mb-1 flex items-center gap-2 text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
            <span>{citation.source_type.replaceAll("_", " ")}</span>
            <span>·</span>
            <span className="truncate">{citation.label}</span>
          </div>
          <p className="line-clamp-4 text-xs leading-5 text-[var(--text-primary)]">{citation.excerpt}</p>
        </div>
      ))}
    </div>
  );
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-lg border border-[var(--line)] bg-[var(--bg-elevated)] p-3">
      <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)]">
        {title}
      </h2>
      {children}
    </section>
  );
}

function ConfidencePill({ confidence }: { confidence: string }) {
  const color = confidence === "low" || confidence === "unknown"
    ? "text-[var(--state-warn)]"
    : "text-[var(--state-success)]";
  const label = confidence === "unknown" ? "No answer yet" : `${confidence} confidence`;
  return (
    <span className={`w-fit rounded border border-[var(--line)] bg-[var(--bg-elevated)] px-2.5 py-1 text-[10px] uppercase tracking-wider ${color}`}>
      {label}
    </span>
  );
}

function StatusDot({ status }: { status: string }) {
  const color = status === "answered"
    ? "bg-[var(--state-success)]"
    : status === "blocked"
      ? "bg-[var(--state-danger)]"
      : "bg-[var(--state-warn)]";
  return <span className={`mt-1 h-2.5 w-2.5 shrink-0 rounded-full ${color}`} />;
}

function executiveFor(value: string): { title: string; remit: string; prompts: string[] } {
  return EXECUTIVE_COPY[value] ?? {
    title: roleTitle(value),
    remit: "Specialist leadership and project context",
    prompts: [
      "What should I know about this area?",
      "What needs my attention next?",
    ],
  };
}

function roleTitle(value: string): string {
  return value
    .replaceAll("_", " ")
    .split(" ")
    .filter(Boolean)
    .map((part) => `${part.slice(0, 1).toUpperCase()}${part.slice(1)}`)
    .join(" ");
}

function delegationStatus(status: string): string {
  switch (status) {
    case "gathering_memory":
      return "reading company memory";
    case "scanning_code":
      return "checking repo evidence";
    case "answered":
      return "answered";
    case "blocked":
      return "blocked";
    default:
      return "queued";
  }
}

function leadershipLanguage(content: string): string {
  return content
    .replaceAll("spokesperson", "leadership room")
    .replaceAll("Spokesperson", "Leadership")
    .replaceAll("I consulted", "I asked")
    .replaceAll("consulted", "asked")
    .replaceAll("Sprint Board SPIKE", "Sprint Board investigation");
}

