import { NextRequest, NextResponse } from "next/server";
import crypto from "node:crypto";
import { sql } from "@/lib/db";

/**
 * GitHub App webhook (P7). Production-only sync: in local dev the App webhook
 * is disabled (localhost isn't reachable) and the install→tenant mapping is
 * done via the Setup-URL redirect instead. Here we verify the signature and
 * clean up on uninstall. This route is in the middleware public allowlist.
 */

function verifySignature(raw: string, sig: string | null, secret: string): boolean {
  if (!sig) return false;
  const expected = "sha256=" + crypto.createHmac("sha256", secret).update(raw).digest("hex");
  const a = Buffer.from(expected);
  const b = Buffer.from(sig);
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}

export async function POST(req: NextRequest) {
  const secret = process.env.MINIONS_GITHUB_APP_WEBHOOK_SECRET;
  if (!secret) {
    return NextResponse.json({ error: "webhook not configured" }, { status: 503 });
  }
  const raw = await req.text();
  if (!verifySignature(raw, req.headers.get("x-hub-signature-256"), secret)) {
    return NextResponse.json({ error: "bad signature" }, { status: 400 });
  }

  const event = req.headers.get("x-github-event");
  const payload = JSON.parse(raw) as {
    action?: string;
    installation?: { id?: number };
  };

  // installation_id is globally unique to a GitHub account install, so we can
  // clean up without knowing the tenant. Repo lists are fetched live, so
  // installation_repositories needs no persisted sync here.
  if (event === "installation" && payload.action === "deleted") {
    const id = payload.installation?.id;
    if (id) {
      await sql()`DELETE FROM tenant_github_installations WHERE installation_id = ${id}`;
    }
  }
  return NextResponse.json({ ok: true });
}
