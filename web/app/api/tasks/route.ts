import { NextRequest, NextResponse } from "next/server";
import { listTasksForDecision, listTasksForProject } from "@/lib/queries";
import { TaskSchema } from "@/lib/schemas";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  try {
    const { searchParams } = new URL(req.url);
    const decisionId = searchParams.get("decision_id");
    const project = searchParams.get("project");
    const sprint = searchParams.get("sprint_number");
    const tasks = decisionId
      ? await listTasksForDecision(decisionId)
      : project
        ? await listTasksForProject(project, sprint ? Number(sprint) : undefined)
        : [];
    return NextResponse.json({ tasks: tasks.map((task) => TaskSchema.parse(task)) });
  } catch (err) {
    console.error("[/api/tasks]", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "internal" },
      { status: 500 },
    );
  }
}
