/**
 * GitHub App credentials — DB-first, env-fallback.
 *
 * The App is created once via the manifest flow (see app/admin/github-app)
 * and its credentials land here instead of env vars, so activating it needs
 * no redeploy. `MINIONS_GITHUB_APP_*` env vars still work as a local-dev
 * override, checked only when no DB row exists.
 */

import { sql } from "./db";

export type GithubAppConfig = {
  appId: string;
  privateKey: string;
  slug: string;
  webhookSecret?: string;
};

type SaveInput = {
  appId: string;
  privateKey: string;
  slug: string;
  webhookSecret?: string;
  clientId?: string;
  clientSecret?: string;
};

async function ensureTable(): Promise<void> {
  const db = sql();
  await db`
    CREATE TABLE IF NOT EXISTS platform_github_app (
      id serial PRIMARY KEY,
      app_id text NOT NULL,
      slug text NOT NULL,
      private_key text NOT NULL,
      webhook_secret text,
      client_id text,
      client_secret text,
      created_at timestamptz NOT NULL DEFAULT now()
    )
  `;
}

export async function getGithubAppConfig(): Promise<GithubAppConfig | null> {
  await ensureTable();
  const db = sql();
  const rows = (await db`
    SELECT app_id, slug, private_key, webhook_secret
    FROM platform_github_app
    ORDER BY created_at DESC
    LIMIT 1
  `) as { app_id: string; slug: string; private_key: string; webhook_secret: string | null }[];

  if (rows[0]) {
    return {
      appId: rows[0].app_id,
      privateKey: rows[0].private_key,
      slug: rows[0].slug,
      webhookSecret: rows[0].webhook_secret ?? undefined,
    };
  }

  const appId = process.env.MINIONS_GITHUB_APP_ID;
  const privateKey = process.env.MINIONS_GITHUB_APP_PRIVATE_KEY;
  const slug = process.env.MINIONS_GITHUB_APP_SLUG;
  if (appId && privateKey && slug) {
    return { appId, privateKey, slug, webhookSecret: process.env.MINIONS_GITHUB_APP_WEBHOOK_SECRET };
  }
  return null;
}

export async function saveGithubAppConfig(input: SaveInput): Promise<void> {
  await ensureTable();
  const db = sql();
  await db`
    INSERT INTO platform_github_app (app_id, slug, private_key, webhook_secret, client_id, client_secret)
    VALUES (${input.appId}, ${input.slug}, ${input.privateKey}, ${input.webhookSecret ?? null},
            ${input.clientId ?? null}, ${input.clientSecret ?? null})
  `;
}
