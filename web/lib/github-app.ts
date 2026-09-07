/**
 * GitHub App auth for the onboarding repo flow (P7 of public-saas-onboarding).
 *
 * Identity is Clerk; *repo access* is this dedicated GitHub App ("minions-saas").
 * We never store a long-lived token — we mint a short-lived **installation
 * access token** on demand from the App's private key:
 *
 *   App JWT (RS256, signed w/ private key)  ->  installation access token
 *   ->  list repos / open PRs as minions[bot]
 *
 * Credentials come from `getGithubAppConfig()` (DB-first, env-fallback —
 * see lib/github-app-config.ts). The App itself is created via the manifest
 * flow at app/admin/github-app, not by hand.
 *
 * Node runtime only (uses node:crypto). Route handlers/RSCs that call this must
 * not be `runtime = "edge"`.
 */

import crypto from "node:crypto";
import { getGithubAppConfig } from "./github-app-config";

const GH_API = "https://api.github.com";
const GH_HEADERS = {
  Accept: "application/vnd.github+json",
  "X-GitHub-Api-Version": "2022-11-28",
};

export type GhRepo = {
  id: number;
  full_name: string;
  name: string;
  default_branch: string;
  private: boolean;
};

function b64url(input: string): string {
  return Buffer.from(input).toString("base64url");
}

/** Mint a ~9-minute App JWT (RS256) signed with the App private key. */
export async function appJwt(): Promise<string> {
  const config = await getGithubAppConfig();
  if (!config) {
    throw new Error("GitHub App not configured — set it up at /admin/github-app");
  }
  const { appId, privateKey: pem } = config;
  const key = pem.includes("\\n") ? pem.replace(/\\n/g, "\n") : pem;
  const now = Math.floor(Date.now() / 1000);
  const header = b64url(JSON.stringify({ alg: "RS256", typ: "JWT" }));
  // iat back-dated 60s to tolerate clock skew; exp must be <= 10 min out.
  const payload = b64url(JSON.stringify({ iat: now - 60, exp: now + 540, iss: appId }));
  const data = `${header}.${payload}`;
  const sig = crypto.sign("RSA-SHA256", Buffer.from(data), key).toString("base64url");
  return `${data}.${sig}`;
}

async function ghApp<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`${GH_API}${path}`, {
    ...init,
    headers: { ...GH_HEADERS, Authorization: `Bearer ${await appJwt()}`, ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  if (!r.ok) {
    throw new Error(`GitHub App API ${path} -> ${r.status} ${await r.text()}`);
  }
  return r.json() as Promise<T>;
}

/** Short-lived installation access token for repo-level calls. */
export async function installationToken(installationId: number): Promise<string> {
  const j = await ghApp<{ token: string }>(
    `/app/installations/${installationId}/access_tokens`,
    { method: "POST" },
  );
  return j.token;
}

/** The GitHub account (user/org login) an installation belongs to. */
export async function installationAccountLogin(installationId: number): Promise<string> {
  const j = await ghApp<{ account: { login: string } }>(
    `/app/installations/${installationId}`,
  );
  return j.account.login;
}

/** Every repo the installation can access (paginated). */
export async function listInstallationRepos(installationId: number): Promise<GhRepo[]> {
  const token = await installationToken(installationId);
  const out: GhRepo[] = [];
  for (let page = 1; ; page++) {
    const r = await fetch(
      `${GH_API}/installation/repositories?per_page=100&page=${page}`,
      { headers: { ...GH_HEADERS, Authorization: `Bearer ${token}` }, cache: "no-store" },
    );
    if (!r.ok) throw new Error(`list repos -> ${r.status} ${await r.text()}`);
    const j = (await r.json()) as { repositories: GhRepo[] };
    for (const repo of j.repositories) {
      out.push({
        id: repo.id,
        full_name: repo.full_name,
        name: repo.name,
        default_branch: repo.default_branch,
        private: repo.private,
      });
    }
    if (j.repositories.length < 100) break;
  }
  return out;
}

export async function installUrl(): Promise<string> {
  const config = await getGithubAppConfig();
  if (!config) throw new Error("GitHub App not configured — set it up at /admin/github-app");
  return `https://github.com/apps/${config.slug}/installations/new`;
}

export async function isGithubAppConfigured(): Promise<boolean> {
  return (await getGithubAppConfig()) !== null;
}
