#!/usr/bin/env node
/**
 * Tenant-scoping tripwire (public-saas-onboarding go-live).
 *
 * Every web query (all of app/ and lib/, route handlers included) must
 * filter every tenant-scoped table by `tenant_id`
 * (the hybrid web side — Postgres RLS guards the Python side). This guard
 * reports every reference to a scoped table with no `tenant_id` filter
 * within 10 lines and, when enforcing, fails the build on any gap.
 *
 *   node scripts/check-tenant-scoping.mjs              # report only (exit 0)
 *   MINIONS_ENFORCE_TENANT_SCOPING=1 node scripts/...  # fail on any gap (exit 1)
 *
 * Read-threading is DONE and this is now enforcing via the `check:tenant-scoping`
 * package script, so a future unscoped query fails. See
 * openspec/changes/public-saas-onboarding.
 */

import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const WEB = join(__dirname, "..");

// The operator-owned tables scoped in migration 0013 + later tenant-scoped
// tables (e.g. agent_profiles in 0016). Keep in sync with the migrations.
const SCOPED_TABLES = [
  "activity_log", "agent_assignments", "agent_chat_messages", "agent_chat_threads",
  "agent_learning", "agent_memory", "agent_profiles", "agile_rituals",
  "audit_findings", "cost_log", "crew_transcripts", "decisions", "deployments",
  "dossier_drafts", "engineer_runs", "interview_consultations", "interview_messages",
  "interview_task_proposals", "interview_threads", "pm_answers", "questions",
  "site_alert_state", "site_health_samples", "sprint_counters", "tasks",
];

// Scan every TypeScript file under app/ and lib/. The guard used to list
// three lib files only, which let unscoped queries in route handlers and
// lib/agent-chat/ through.
function walk(dir) {
  return readdirSync(join(WEB, dir), { withFileTypes: true }).flatMap((e) => {
    const rel = `${dir}/${e.name}`;
    if (e.isDirectory()) return walk(rel);
    return /\.tsx?$/.test(e.name) ? [rel] : [];
  });
}
const FILES = [...walk("app"), ...walk("lib")].sort();
const WINDOW = 10;
const tableAlt = SCOPED_TABLES.join("|");
const refRe = new RegExp(`\\b(?:FROM|JOIN|INTO|UPDATE)\\s+(${tableAlt})\\b`, "i");

let refs = 0;
const gaps = [];

for (const rel of FILES) {
  let text;
  try {
    text = readFileSync(join(WEB, rel), "utf8");
  } catch {
    continue; // file may not exist (e.g. mutations.ts)
  }
  const lines = text.split("\n");
  lines.forEach((line, i) => {
    // Skip comment lines — prose like "derive state from activity_log" trips
    // the case-insensitive FROM/JOIN regex but isn't a real query.
    const trimmed = line.trim();
    if (trimmed.startsWith("*") || trimmed.startsWith("//") || trimmed.startsWith("/*")) {
      return;
    }
    const m = line.match(refRe);
    if (!m) return;
    refs++;
    const lo = Math.max(0, i - WINDOW);
    const hi = Math.min(lines.length, i + WINDOW + 1);
    const near = lines.slice(lo, hi).join("\n");
    // An intentional cross-tenant query (e.g. a service-wide spend cap) must
    // say so with a `tenant-scoping: allow-global` comment next to it.
    if (!/tenant_id|tenant-scoping: allow-global/.test(near)) {
      gaps.push(`${rel}:${i + 1}  ${m[0]} — no tenant_id within ${WINDOW} lines`);
    }
  });
}

const enforce = process.env.MINIONS_ENFORCE_TENANT_SCOPING === "1";
console.log(`tenant-scoping: ${refs} scoped-table refs, ${gaps.length} without a tenant_id filter`);
if (gaps.length) {
  for (const g of gaps) console.log(`  ⚠ ${g}`);
  if (enforce) {
    console.error("\nFAIL: unscoped tenant tables (MINIONS_ENFORCE_TENANT_SCOPING=1).");
    process.exit(1);
  }
  console.log("\n(non-enforcing) Read-threading is deferred to pre-P6. Flip MINIONS_ENFORCE_TENANT_SCOPING=1 once done.");
}
