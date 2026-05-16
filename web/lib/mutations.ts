/**
 * Operator write paths. Each function mutates Postgres directly through
 * the same connection the read queries use. Called only from POST route
 * handlers — never from React components.
 */

import { sql } from "./db";

export async function approveDecision(
  decisionId: string,
  reason?: string,
): Promise<void> {
  const s = sql();
  await s`
    UPDATE decisions
    SET
      status = 'approved',
      resolved_at = NOW(),
      payload = jsonb_set(
        jsonb_set(payload, '{status}', '"approved"'::jsonb),
        '{resolved_reason}',
        to_jsonb(${reason ?? "approved via operator console"}::text)
      )
    WHERE id = ${decisionId}::uuid
  `;
}

export async function rejectDecision(
  decisionId: string,
  reason?: string,
): Promise<void> {
  const s = sql();
  await s`
    UPDATE decisions
    SET
      status = 'rejected',
      resolved_at = NOW(),
      payload = jsonb_set(
        jsonb_set(payload, '{status}', '"rejected"'::jsonb),
        '{resolved_reason}',
        to_jsonb(${reason ?? "rejected via operator console"}::text)
      )
    WHERE id = ${decisionId}::uuid
  `;
}

/**
 * Pre-flight check for the auto-merge button: load the Decision row from
 * Postgres and confirm the same conditions the SprintCard computed
 * client-side. Mirrors `can_auto_merge` so a malicious or stale POST can't
 * bypass it.
 */
export async function loadMergeContext(decisionId: string): Promise<{
  ok: boolean;
  reason?: string;
  project?: string;
  pr_number?: number;
  pr_url?: string;
}> {
  const s = sql();
  const rows = (await s`
    SELECT
      d.project,
      d.status,
      d.risk,
      er.pr_url,
      (er.payload->>'pr_number')::int AS pr_number,
      er.payload->>'ci_conclusion' AS ci_conclusion,
      er.pr_state
    FROM decisions d
    LEFT JOIN engineer_runs er ON er.decision_id = d.id::text
    WHERE d.id = ${decisionId}::uuid
    LIMIT 1
  `) as Array<{
    project: string;
    status: string;
    risk: string;
    pr_url: string | null;
    pr_number: number | null;
    ci_conclusion: string | null;
    pr_state: string | null;
  }>;
  if (rows.length === 0) return { ok: false, reason: "decision not found" };
  const r = rows[0];
  if (r.status !== "executed") return { ok: false, reason: `status is ${r.status}, not executed` };
  if (r.risk !== "low") return { ok: false, reason: `risk is ${r.risk}, not low` };
  if (r.ci_conclusion !== "success")
    return { ok: false, reason: `ci is ${r.ci_conclusion ?? "unknown"}, not success` };
  if (!r.pr_number || !r.pr_url) return { ok: false, reason: "no associated PR" };
  if (r.pr_state === "merged") return { ok: false, reason: "already merged" };
  return {
    ok: true,
    project: r.project,
    pr_number: r.pr_number,
    pr_url: r.pr_url,
  };
}

/**
 * Merge a PR via GitHub's API. The Python agent client deliberately has
 * NO merge method; the operator console is allowed to merge because the
 * decision to do so is a human action, gated by `loadMergeContext`.
 *
 * Returns the GitHub merge response or throws.
 */
export async function mergePullRequest(args: {
  prUrl: string;
  prNumber: number;
}): Promise<{ sha: string; merged: boolean }> {
  const token = process.env.MINIONS_GH_PAT ?? process.env.GITHUB_TOKEN;
  if (!token) {
    throw new Error("MINIONS_GH_PAT not set; cannot merge");
  }
  // Parse owner/repo from the PR URL: https://github.com/owner/repo/pull/N
  const m = /github\.com\/([^/]+)\/([^/]+)\/pull\//.exec(args.prUrl);
  if (!m) throw new Error(`unparseable PR URL: ${args.prUrl}`);
  const [, owner, repo] = m;

  const res = await fetch(
    `https://api.github.com/repos/${owner}/${repo}/pulls/${args.prNumber}/merge`,
    {
      method: "PUT",
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ merge_method: "squash" }),
    },
  );
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`GitHub merge ${res.status}: ${text.slice(0, 200)}`);
  }
  const body = (await res.json()) as { sha: string; merged: boolean };

  // Persist the merged state locally so the board reflects it without
  // waiting for the daily PR sync to run.
  const s = sql();
  await s`
    UPDATE engineer_runs
    SET pr_state = 'merged',
        payload = jsonb_set(payload, '{pr_state}', '"merged"'::jsonb)
    WHERE pr_url = ${args.prUrl}
  `;
  return body;
}
