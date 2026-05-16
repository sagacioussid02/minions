import { NextRequest, NextResponse } from "next/server";
import { loadMergeContext, mergePullRequest } from "@/lib/mutations";

export const dynamic = "force-dynamic";

export async function POST(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  try {
    const { id } = await params;
    const ctx = await loadMergeContext(id);
    if (!ctx.ok) {
      return NextResponse.json(
        { ok: false, reason: ctx.reason ?? "merge blocked" },
        { status: 409 },
      );
    }
    const result = await mergePullRequest({
      prUrl: ctx.pr_url!,
      prNumber: ctx.pr_number!,
    });
    return NextResponse.json({ ok: true, ...result });
  } catch (err) {
    console.error("[merge]", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "internal" },
      { status: 500 },
    );
  }
}
