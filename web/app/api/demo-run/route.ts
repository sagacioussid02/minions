import { NextResponse } from "next/server";
import { listRecentEvents } from "@/lib/queries";
import { type ActivityEvent } from "@/lib/schemas";

export const dynamic = "force-dynamic";

export async function POST() {
  try {
    const recent = await listRecentEvents({ limit: 20 });
    const seeds = recent.filter((event) => event.project || event.role || event.crew);
    const project = seeds.find((event) => event.project)?.project ?? "demo-project";
    const roles = unique(
      seeds
        .map((event) => event.role ?? event.crew)
        .filter((role): role is string => Boolean(role)),
    );
    const crew = roles.length > 0 ? roles : ["product_owner", "tech_team_lead", "engineer", "qa_engineer"];
    const runId = `demo-${Date.now()}`;
    const now = Date.now();

    const steps: Array<{
      event: string;
      role: string;
      crew: string;
      offsetMs: number;
      summary: string;
    }> = [
      {
        event: "crew_started",
        role: crew[0] ?? "product_owner",
        crew: "planning",
        offsetMs: 0,
        summary: "Demo run started from the investor console",
      },
      {
        event: "decision_submitted",
        role: crew[1] ?? "tech_team_lead",
        crew: "engineering",
        offsetMs: 1_400,
        summary: "Crew shaped a small implementation plan",
      },
      {
        event: "pr_opened",
        role: crew[2] ?? "engineer",
        crew: "engineering",
        offsetMs: 2_800,
        summary: "Engineer prepared a safe simulated PR step",
      },
      {
        event: "audit_finding_created",
        role: crew[3] ?? "qa_engineer",
        crew: "qa",
        offsetMs: 4_200,
        summary: "QA reviewed the demo work path",
      },
      {
        event: "crew_finished",
        role: crew[0] ?? "product_owner",
        crew: "planning",
        offsetMs: 5_600,
        summary: "Demo run completed without GitHub writes",
      },
    ];

    const events: ActivityEvent[] = steps.map((step, index) => ({
      id: -1 * (now + index),
      ts: new Date(now + step.offsetMs).toISOString(),
      event: step.event,
      project,
      role: step.role,
      decision_id: null,
      decision_summary: step.summary,
      crew: step.crew,
      run_id: runId,
      error: null,
      payload: {
        demo_mode: true,
        safe: true,
        github_writes: false,
        summary: step.summary,
        agents: [step.role],
      },
    }));

    return NextResponse.json({ mode: "safe-demo", writes_github: false, events });
  } catch (err) {
    console.error("[/api/demo-run]", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "internal" },
      { status: 500 },
    );
  }
}

function unique(values: string[]): string[] {
  return [...new Set(values)];
}
