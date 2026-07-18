import { NextRequest, NextResponse } from "next/server";
import { approveDecision } from "@/lib/mutations";
import { getCurrentTenant } from "@/lib/tenant";
import { dispatchTenantWorkflow } from "@/lib/actions-dispatch";

export const dynamic = "force-dynamic";

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  try {
    const { id } = await params;
    let body: { reason?: string } = {};
    try {
      body = await req.json();
    } catch {
      /* no body is fine */
    }
    await approveDecision(id, body.reason);

    // Sandbox/tenant approvals dispatch a tenant-scoped execute-approved
    // sweep immediately, so the real draft PR appears in minutes instead of
    // waiting for the founder's own Mon/Wed/Fri shared cron. The founder's
    // own approvals already ride that shared cron — no dispatch needed.
    const tenant = await getCurrentTenant();
    if (!tenant.founder) {
      await dispatchTenantWorkflow("tenant_execute_approved.yml", {
        tenant_id: tenant.tenant_id,
      });
    }

    return NextResponse.json({ ok: true, id });
  } catch (err) {
    console.error("[approve]", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "internal" },
      { status: 500 },
    );
  }
}
