import type { CrewAgent, CrewDashboard, CrewProject } from "@/lib/crew-dashboard";
import { prettyRole } from "@/lib/roles";

const QUEUE: [keyof CrewProject["queue"], string][] = [
  ["needs_approval", "need approval"],
  ["ready", "ready"],
  ["in_progress", "in progress"],
  ["blocked", "blocked"],
];

export function CrewBoard({
  data,
  meetings,
}: {
  data: CrewDashboard;
  meetings: { label: string; when: string }[];
}) {
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-3 text-xs text-[var(--text-muted)]">
        <span>
          {data.slots_used_this_week} work slot{data.slots_used_this_week === 1 ? "" : "s"} used
          this week
        </span>
        <span>{meetings.map((m) => `${m.label} ${m.when}`).join(" · ")}</span>
      </div>

      {data.projects.length === 0 && (
        <p className="text-sm text-[var(--text-muted)]">
          No projects with a crew yet. Once a project has an approved charter it appears here.
        </p>
      )}
      {data.projects.map((p) => (
        <ProjectRow key={p.project} p={p} />
      ))}

      {data.shared.length > 0 && (
        <section aria-label="Shared specialists" className="flex flex-col gap-3">
          <h2 className="text-xs uppercase tracking-wider text-[var(--text-muted)]">
            Shared specialists
          </h2>
          <div className="grid gap-3 sm:grid-cols-2">
            {data.shared.map((a) => (
              <AgentCard key={a.agent_id} a={a} />
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

function ProjectRow({ p }: { p: CrewProject }) {
  return (
    <section
      aria-label={p.project}
      className="rounded-xl border border-[var(--line)] bg-[var(--bg-elevated)] p-4"
    >
      <div className="mb-1 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="font-mono text-base text-[var(--accent)]">{p.project}</h2>
        <span className="text-xs text-[var(--text-muted)]">
          {p.product_owner ? `Accountable: ${p.product_owner}` : "No product owner yet"}
        </span>
      </div>
      <p className="mb-3 text-sm">{p.goal ?? "No active charter yet."}</p>
      <dl className="mb-4 flex flex-wrap gap-x-5 gap-y-1 text-xs">
        {QUEUE.map(([key, label]) => (
          <div key={key} className="flex gap-1">
            <dt className="text-[var(--text-muted)]">{label}</dt>
            <dd className="font-mono">{p.queue[key]}</dd>
          </div>
        ))}
      </dl>
      {p.agents.length === 0 ? (
        <p className="text-xs text-[var(--text-muted)]">The crew appears after its first run.</p>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2">
          {p.agents.map((a) => (
            <AgentCard key={a.agent_id} a={a} />
          ))}
        </div>
      )}
    </section>
  );
}

function AgentCard({ a }: { a: CrewAgent }) {
  return (
    <article className="rounded-lg border border-[var(--line)] bg-[var(--bg-surface)] p-3">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-sm font-medium">{a.name}</span>
        <span className="text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
          {prettyRole(a.role)}
        </span>
      </div>
      {a.expertise.length > 0 && (
        <p className="mt-1 text-xs text-[var(--text-muted)]">{a.expertise.slice(0, 4).join(" · ")}</p>
      )}
      <p className="mt-2 text-xs">
        {a.current_issue ? (
          // Issue titles are user/GitHub content: rendered as text, link only when https.
          a.current_issue.url.startsWith("https://") ? (
            <a
              href={a.current_issue.url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-[var(--accent)] hover:underline"
            >
              #{a.current_issue.number} {a.current_issue.title}
            </a>
          ) : (
            <span>
              #{a.current_issue.number} {a.current_issue.title}
            </span>
          )
        ) : (
          <span className="text-[var(--text-muted)]">idle</span>
        )}
      </p>
      <p className="mt-1 text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
        {a.slots_used} slot{a.slots_used === 1 ? "" : "s"} this week
      </p>
    </article>
  );
}
