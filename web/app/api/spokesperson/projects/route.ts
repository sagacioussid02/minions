import { NextResponse } from "next/server";
import { listSpokespersonProjects } from "@/lib/spokesperson";
import { SpokespersonProjectsSchema } from "@/lib/schemas";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const projects = await listSpokespersonProjects();
    return NextResponse.json(SpokespersonProjectsSchema.parse({ projects }));
  } catch (err) {
    console.error("[/api/spokesperson/projects]", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "internal" },
      { status: 500 },
    );
  }
}
