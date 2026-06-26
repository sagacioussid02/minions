import Link from "next/link";
import { notFound } from "next/navigation";
import { HeartbeatDot } from "@/components/HeartbeatDot";
import { listTranscriptByRun } from "@/lib/queries";

export const dynamic = "force-dynamic";

const ROLE_BADGE: Record<string, string> = {
  pitch: "border-cyan-400/50 text-cyan-200 bg-cyan-400/10",
  rebuttal: "border-amber-400/50 text-amber-200 bg-amber-400/10",
  synthesis: "border-emerald-400/50 text-emerald-200 bg-emerald-400/10",
  review: "border-fuchsia-400/50 text-fuchsia-200 bg-fuchsia-400/10",
  task_output: "border-zinc-500/50 text-zinc-300 bg-zinc-500/10",
  other: "border-[var(--line)] text-[var(--text-muted)]",
};

const PHASE_LABEL: Record<string, string> = {
  pitch: "pitch",
  rebuttal: "rebuttal",
  synthesis: "synthesis",
  review: "review",
  task_output: "contribution",
  other: "spoke",
};

export default async function TranscriptPage({
  params,
}: {
  params: Promise<{ run_id: string }>;
}) {
  const { run_id } = await params;
  const messages = await listTranscriptByRun(decodeURIComponent(run_id));
  if (messages.length === 0) notFound();

  const project = messages[0]?.project ?? "";
  const crew = messages[0]?.crew ?? "";
  const startedAt = messages[0]?.created_at;
  const endedAt = messages[messages.length - 1]?.created_at;

  return (
    <div className="relative flex min-h-screen flex-col">
      <header className="relative flex items-center justify-between border-b border-[var(--line)] px-6 py-3">
        <div className="flex items-center gap-3">
          <Link
            href="/hq"
            className="font-mono text-sm tracking-tight text-[var(--accent)]"
          >
            ⌬ minions
          </Link>
          <Link
            href="/hq/stage"
            className="text-xs text-[var(--text-muted)] hover:text-[var(--text-primary)]"
          >
            stage
          </Link>
          <span className="text-xs text-[var(--text-muted)]">
            /transcripts/{run_id.slice(0, 8)}
          </span>
        </div>
        <HeartbeatDot />
      </header>
      <main className="relative flex-1 px-6 py-6">
        <section className="mb-6 rounded-lg border border-[var(--line)] bg-[var(--surface-1)] p-5">
          <div className="font-mono text-lg tracking-tight">
            {crew} crew · {project}
          </div>
          <div className="mt-1 text-xs text-[var(--text-muted)]">
            {messages.length} message{messages.length === 1 ? "" : "s"}
            {startedAt && (
              <>
                {" · "}
                started {new Date(startedAt).toLocaleString()}
              </>
            )}
            {endedAt && endedAt !== startedAt && (
              <>
                {" · "}
                ended {new Date(endedAt).toLocaleString()}
              </>
            )}
          </div>
          <div className="mt-2 font-mono text-[10px] text-[var(--text-muted)]">
            run {run_id}
          </div>
        </section>

        <ol className="space-y-3">
          {messages.map((m) => (
            <li
              key={m.id}
              id={`msg-${m.sequence}`}
              className="rounded-lg border border-[var(--line)] bg-[var(--surface-1)] p-4 scroll-mt-24"
            >
              <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
                <span
                  className={`rounded border px-2 py-0.5 text-[10px] uppercase tracking-wider ${ROLE_BADGE[m.role_in_conversation] ?? ROLE_BADGE.other}`}
                >
                  {PHASE_LABEL[m.role_in_conversation] ?? "spoke"}
                </span>
                <span className="font-mono text-sm text-[var(--text-primary)]">
                  {m.agent_display_name ?? m.agent_role}
                </span>
                <span className="text-[10px] text-[var(--text-muted)]">
                  ({m.agent_role.replaceAll("_", " ")})
                </span>
                <span className="ml-auto text-[10px] text-[var(--text-muted)]">
                  #{m.sequence} · {new Date(m.created_at).toLocaleTimeString()}
                </span>
              </div>
              <pre className="overflow-x-auto whitespace-pre-wrap break-words text-xs text-[var(--text-primary)]">
                {m.content}
              </pre>
            </li>
          ))}
        </ol>
      </main>
    </div>
  );
}
