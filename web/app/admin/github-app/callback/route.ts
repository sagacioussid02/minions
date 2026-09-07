import { NextRequest, NextResponse } from "next/server";
import { getCurrentTenant } from "@/lib/tenant";
import { saveGithubAppConfig } from "@/lib/github-app-config";

/**
 * GitHub App manifest flow callback. GitHub redirects here with a one-time
 * `code` after the operator confirms App creation on github.com; we exchange
 * it for the App's real credentials (no auth header needed — the code itself
 * is the credential) and persist them via saveGithubAppConfig.
 */
export async function GET(req: NextRequest) {
  const tenant = await getCurrentTenant();
  if (!tenant.founder) {
    return NextResponse.json({ error: "forbidden" }, { status: 403 });
  }

  const code = req.nextUrl.searchParams.get("code");
  if (!code) {
    return NextResponse.json({ error: "missing code" }, { status: 400 });
  }

  const r = await fetch(`https://api.github.com/app-manifests/${code}/conversions`, {
    method: "POST",
    headers: { Accept: "application/vnd.github+json" },
  });
  if (!r.ok) {
    return NextResponse.json(
      { error: "manifest conversion failed", detail: await r.text() },
      { status: 502 },
    );
  }

  const app = (await r.json()) as {
    id: number;
    slug: string;
    pem: string;
    webhook_secret: string | null;
    client_id: string;
    client_secret: string;
  };

  await saveGithubAppConfig({
    appId: String(app.id),
    slug: app.slug,
    privateKey: app.pem,
    webhookSecret: app.webhook_secret ?? undefined,
    clientId: app.client_id,
    clientSecret: app.client_secret,
  });

  return NextResponse.redirect(new URL("/admin/github-app?success=1", req.url));
}
