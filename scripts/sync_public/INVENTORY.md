# Public-parity inventory (minions-org → minions-public)

Snapshot taken: 2026-05-25. Scope: source code (`src/`, `tests/`), config (`config/`), workflows (`.github/`), project manifests (`projects/`), root files (`README.md`, `pyproject.toml`, `.gitignore`).

**Out of scope** (already excluded by `diff -rq` filters): `.venv/`, `.mypy_cache/`, `.pytest_cache/`, `__pycache__/`, `data/local/`, `data/dossiers/`, `node_modules/`, `.next/`, `.git/`.

## Tag legend

- **SHIP** — copy verbatim from minions-org.
- **SCRUB** — copy with a documented transform. Rule listed inline.
- **DROP** — never sync; either use the existing minions-public version or omit entirely.

## Summary

| Bucket | Count |
|---|---|
| Only in private (would ship) | 109 paths |
| Only in public (preserve as-is) | 20 paths |
| Differing files | 81 files |

---

## 1. Only in private — subsystems missing from public

These are entire features that haven't been published yet. **Most are SHIP-AS-IS** because the code itself is provider-neutral; sensitive content only lives in `config/` and `projects/`.

### 1a. Workflows — SCRUB (cron-cadence + secrets)

All `.github/workflows/*.yml` files use `_cron-shared.yml` to access GitHub secrets. The workflows themselves are scrubbed via shared template; per-workflow content is safe to ship. **Rule:** copy verbatim; the existing `_cron-shared.yml` on public must continue to gate on `secrets.ANTHROPIC_API_KEY` etc. without any hardcoded values.

- `.github/workflows/agent_memory_demote.yml` — SHIP
- `.github/workflows/assign_backlog_tasks.yml` — SHIP
- `.github/workflows/branch_sweep.yml` — SHIP
- `.github/workflows/crew_heartbeat.yml` — SHIP
- `.github/workflows/daily.yml` — SHIP
- `.github/workflows/discovery.yml` — SHIP
- `.github/workflows/execute_expedited.yml` — SHIP
- `.github/workflows/friday.yml` — SHIP
- `.github/workflows/monthly.yml` — SHIP
- `.github/workflows/post_deploy_verify.yml` — SHIP
- `.github/workflows/pr_owner_sweep.yml` — SHIP
- `.github/workflows/pr_review_loop.yml` — SHIP
- `.github/workflows/project_scan.yml` — SHIP
- `.github/workflows/refine_approved.yml` — SHIP
- `.github/workflows/scrum.yml` — SHIP
- `.github/workflows/weekly.yml` — SHIP

### 1b. Operator artefacts — DROP

- `.git/AUTO_MERGE` — local git state, never sync. DROP.
- `.ruff_cache/**` — build artefact. DROP.

### 1c. Project manifests — DROP (public has demo equivalents)

Public already ships `projects/demo.yaml` … `projects/demo_five.yaml`. The real ones never go to public.

- `projects/Demo.yaml` — DROP
- `projects/demo_two.yaml` — DROP
- `projects/demo_four.yaml` — DROP
- `projects/demo_five.yaml` — DROP
- `projects/demo_three.yaml` — DROP
- `projects/_deferred/` — DROP

### 1d. Agent-name registry — SCRUB

- `config/agent_names.yaml` — **SCRUB.** Keyed by project name (e.g. `engineer@Demo`, `product_owner@demo_three`); the project-name text replacements handle the keys, agent names themselves (Aria, Mira, Vera, etc.) are fictional and stay verbatim. Confirmed via grep 2026-05-25.

### 1e. Source modules — SHIP (all 48 new modules)

Provider-neutral code. No personal references in any of these per Phase A grep.

```
src/minions/agents/memory.py                      SHIP
src/minions/agents/memory_store.py                SHIP
src/minions/agents/memory_store_factory.py        SHIP
src/minions/agents/memory_store_postgres.py       SHIP
src/minions/agents/naming.py                      SHIP
src/minions/agile/**                              SHIP (entire dir)
src/minions/crews/backlog_proposer.py             SHIP
src/minions/crews/discoverer.py                   SHIP
src/minions/crews/flow_control.py                 SHIP
src/minions/crews/portfolio_review.py             SHIP
src/minions/crews/pr_reviewer.py                  SHIP
src/minions/crews/refinement.py                   SHIP
src/minions/db/migrations/0004_agent_learning.sql SHIP
src/minions/db/migrations/0005_sprint_tasks_memory.sql SHIP
src/minions/db/migrations/0006_unassigned_task_status.sql SHIP
src/minions/db/migrations/0007_dossier_drafts.sql SHIP
src/minions/db/migrations/0008_crew_transcripts.sql SHIP
src/minions/db/migrations/0009_deployments.sql    SHIP
src/minions/deployments/**                        SHIP (entire dir — but see scrub for verifier.py refs)
src/minions/dossiers/**                           SHIP (entire dir)
src/minions/learning/**                           SHIP (entire dir)
src/minions/models/agent_memory.py                SHIP
src/minions/models/agile.py                       SHIP
src/minions/models/backlog.py                     SHIP
src/minions/models/deployment.py                  SHIP
src/minions/models/dossier.py                     SHIP
src/minions/models/interview.py                   SHIP
src/minions/models/learning.py                    SHIP
src/minions/models/sprint_plan.py                 SHIP
src/minions/models/task.py                        SHIP
src/minions/models/transcript.py                  SHIP
src/minions/preflight/**                          SHIP (entire dir)
src/minions/scheduled/agent_memory_demote.py      SHIP
src/minions/scheduled/assign_backlog_tasks.py     SHIP
src/minions/scheduled/branch_sweep.py             SHIP
src/minions/scheduled/crew_heartbeat.py           SHIP
src/minions/scheduled/discovery.py                SHIP
src/minions/scheduled/monthly_portfolio_review.py SHIP
src/minions/scheduled/post_deploy_verify.py       SHIP
src/minions/scheduled/pr_owner_sweep.py           SCRUB (grep refs to Demo/demo_three/demo_four in comments)
src/minions/scheduled/pr_review_loop.py           SHIP
src/minions/scheduled/refine_approved.py          SHIP
src/minions/scheduled/scrum.py                    SCRUB (grep refs)
src/minions/spokesperson/**                       SCRUB (interview_relay.py mentions demo_five)
src/minions/sprints/**                            SHIP (entire dir)
src/minions/tasks/**                              SHIP (entire dir)
src/minions/transcripts/**                        SHIP (entire dir)
src/minions/working_tree.py                       SHIP
```

### 1f. Tests — SHIP

All 32 new test files are provider-neutral and use fixtures, not real project refs. SHIP all of them. (Confirmed: grep for sensitive strings turned up nothing in tests during inventory.)

---

## 2. Differing files — sync newer version (mostly SHIP)

81 files. Categorized below.

### 2a. SCRUB (10 files — contain personal refs in comments / examples / UI strings)

| Path | Scrub rule |
|---|---|
| `README.md` | Replace project name list, repo URLs, command examples, "operator@example.com", "your-github-org" with public placeholders. Public version is already scrubbed — diff and bring forward feature docs but keep public's preamble + onboarding section. |
| `config/portfolio.yaml` | Public version is already the demo. Bring forward any new top-level keys (e.g. new cadence_profiles), but keep public's `owner:`, `projects:`, `email_alias_template:`. |
| `src/minions/__main__.py` | Strip "(e.g., Demo)" docstring examples → "(e.g., demo)". Replace "demo_five" → "demo" in example commands. |
| `src/minions/activity.py` | Module-docstring example mentions "demo_five" — replace with "demo". |
| `src/minions/cost.py` | Same — replace example project in module docstring. |
| `src/minions/observability.py` | Module docstring mentions `Demo` — replace with `demo`. |
| `src/minions/dashboard/app.py` | Line 182 has hardcoded `operator@example.com` caption. **Replace with `os.getenv("OPERATOR_EMAIL", "operator@example.com")` or pull from portfolio.yaml.** |
| `src/minions/spokesperson/interview_relay.py` | Example refs. |
| `src/minions/deployments/verifier.py` | Probable refs (in only-in-private bucket but flagged for symmetry). |
| `src/minions/scheduled/pr_owner_sweep.py`, `scrum.py` | Comment refs. |

**Scrub transforms (one script, applied to all SCRUB files):**

```yaml
# In scripts/sync_public/scrub_rules.yaml
text_replacements:
  - find: "operator@example.com"
    replace: "operator@example.com"
  - find: "your-github-org"
    replace: "your-github-org"
  - find: "Demo"
    replace: "demo"          # case-insensitive
  - find: "demo_two"
    replace: "demo-two"
  - find: "demo_three"
    replace: "demo-three"
  - find: "demo_four"
    replace: "demo-four"
  - find: "demo_five"
    replace: "demo-five"
  - find: "Demo"
    replace: "Demo"
  - find: "demo_two"
    replace: "demo-two"
  - find: "demo_three"
    replace: "demo-three"
```

Plus one structural replacement for `dashboard/app.py:182` (hardcoded email → portfolio lookup).

### 2b. SHIP-AS-IS (71 files — provider-neutral source + tests)

Full list omitted for brevity; see `diffs_real.txt` minus the SCRUB rows above. The differences are feature work that has no personal references.

Notable groups:
- All `src/minions/approval/*.py` updates
- All `src/minions/crews/*.py` updates (except where SCRUB'd above)
- All `src/minions/dashboard/*.py` (except `app.py`)
- All `src/minions/github/*.py`
- All `src/minions/models/*.py` updates
- All `src/minions/notify/*.py`
- All `src/minions/scheduled/*.py` (except where SCRUB'd above)
- All `src/minions/webhook/*.py`
- All `tests/test_*.py` updates

### 2c. SHIP-WITH-MERGE (4 files — public has its own version we must preserve)

| Path | Strategy |
|---|---|
| `.gitignore` | Union both. Public has open-source-project entries (`.idea/`, etc.). |
| `pyproject.toml` | Bring private's deps/scripts forward; keep public's project metadata (name, version, classifiers, URLs). |
| `.github/workflows/_cron-shared.yml` | Public is already scrubbed for secrets. Bring forward any new env vars but preserve public's secret-name conventions. |
| `.github/workflows/pr_followup.yml` | Same pattern. |

---

## 3. Only in public — preserve

Don't overwrite or drop these in the sync. They are intentionally public-only.

| Path | Reason |
|---|---|
| `.github/CODEOWNERS` | Public-repo governance |
| `.github/ISSUE_TEMPLATE/` | Public-repo contribution surface |
| `.github/dependabot.yml` | Public-repo automation |
| `.github/pull_request_template.md` | Public-repo contribution surface |
| `.github/workflows/ci.yml` | Public CI config (different from private cron workflows) |
| `.github/workflows/release.yml` | Public release automation |
| `.github/workflows/welcome.yml` | Public greeting bot |
| `projects/demo.yaml` … `projects/demo_five.yaml` | Public's demo manifests; never overwrite |
| `tests/test_cli_decisions.py` | Public-only smoke test |
| `tests/test_guardrail_events.py` | Public-only smoke test |
| `.ruff_cache/**` | Build artefact — ignore on both sides |

---

## 4. Manual review pile (before sync runs)

Before the sync script executes, the operator should eyeball these:

- [ ] `config/agent_names.yaml` — confirm it has no personal data.
- [ ] `projects/_deferred/` — confirm content is not needed for demo (likely yes — these are paused real projects).
- [ ] `src/minions/__main__.py` — there may be CLI command examples in `--help` strings that aren't caught by the simple text-replacement scrubs. Re-run grep on the scrubbed output before commit.
- [ ] `README.md` — re-write by hand. Auto-scrub won't produce a polished public README; this is the operator's marketing surface.
- [ ] `src/minions/dashboard/app.py:182` — confirm the public version reads from env/config, not a hardcoded constant.
- [ ] `.github/workflows/_cron-shared.yml` — confirm secret names match public's expected secrets (operator likely uses different secret names locally vs. demo deployment).

---

## 5. Output of Phase A

This file. Phase B (sync script) consumes it.

**Next step:** scripts/sync_public/sync_to_public.py
- reads INVENTORY.md (or a derived `manifest.yaml` — easier to parse)
- reads `scrub_rules.yaml`
- copies SHIP files, applies SCRUB transforms, skips DROP files
- writes into `--target` working tree
- dry-run by default; `--apply` mutates the target tree
