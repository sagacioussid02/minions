# Minions

Autonomous AI engineering organization that owns and maintains a portfolio of software projects, with the operator as the sole human-in-the-loop approver.

See `openspec/changes/bootstrap-agent-org/` for the full design and phased plan.

## Status

**v0 frugal mode** — Phase 2 (org skeleton), Phase 2.5 (CrewAI integration), and Phase 3 (LangGraph approval graph + console notifier) all bootstrapped and end-to-end tested. Phase 1 foundations (GitHub App, AWS, branch protection, Gmail OAuth) need operator console clicks; see Day-1 checklist below.

Active managed projects (manifests in `projects/`):

| Project | Repo | Monthly cap |
|---|---|---|
| Demo | your-github-org/Demo | $4 |
| demo_two | your-github-org/demo_two | $4 |
| demo_three | your-github-org/demo_three | $4 |
| demo_four | TBD | $2 |
| trading | _deferred_ | _$8 when re-added_ |

**Total v0 budget:** ≈ $14/month (envelope $15–$30).

## What's implemented

- Project structure (`pyproject.toml`, src-layout, ruff/mypy/pytest config)
- Pydantic data models: `Decision`, `AuditFinding`, `Manifest`, `PortfolioConfig`
- Role registry with target + v0 frugal tier mappings (Opus / Sonnet / Haiku)
- Manifest loader (skips `_deferred/`) + portfolio config loader
- Base `MinionAgent` class with safety preamble (the four hard rules) injected into every system prompt
- **CrewAI integration** — `make_crewai_agent()` factory translates `MinionAgent` → `crewai.Agent` with the right LLM tier
- **Planning crew** — sequential PO → Principal → Manager that produces a sprint Decision Record (dry-run + real modes)
- **Decision Store** — JSON-file backed (`data/local/decisions.json`); swaps for Neon Postgres in Phase 6
- **Approval service** — `submit_for_approval()` + `resolve()` with status updates and notifier callbacks
- **LangGraph approval graph** — durable in-process state machine (notify → interrupt → resolve), with `InMemorySaver` for v0 (swap to `SqliteSaver`/`PostgresSaver` later)
- **HMAC-signed magic-link tokens** — 72h TTL, sign/verify with timing-safe compare
- **Notifier abstraction** — `ConsoleNotifier` (v0 demo) + `GmailNotifier` (stub for OAuth-later)
- **Secrets resolver** — env vars in v0 (`MINIONS_SECRET_*`), AWS Secrets Manager seam for production
- **CLI**: `minions check`, `org`, `roster`, `plan <project>`, `decisions list/show/approve/reject`
- **Test suite** — 47 tests covering models, manifests, config, agent assembly, planning crew (dry-run), Decision Store, approval service, approval graph (interrupt+resume), token sign/verify, secrets

## What's NOT implemented yet

- Real Gmail send (stub raises `NotImplementedError` — needs OAuth in Phase 1.8 / 3.3)
- GitHub App + branch-protection enforcement (Phase 1)
- AWS Secrets Manager wrapper (Phase 7; secrets module has the seam)
- Neon Postgres audit log (Phase 1.6 + 6.1; JSON store in the meantime)
- Langfuse traces (Phase 1.5)
- Modal / Fly.io deployment (Phase 1.7)
- Audit team agents wired up (Phase 9)
- Procurement / team-composition Decision flows (Phases 10–11)
- Engineer crew that picks up an approved sprint and opens PRs
- Cost-accounting middleware (Phase 6.1)

## Dev setup

Recommend [uv](https://github.com/astral-sh/uv) (fast). Pip works too.

```bash
# with uv
cd minions
uv venv
uv pip install -e ".[dev]"

# OR with pip
cd minions
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### Run the CLI

```bash
minions check                        # validate config/portfolio.yaml + all manifests
minions org                          # print full org topology with model tiers
minions roster Demo                  # roster for a single project (or omit for all)

# Planning + approval flow (end-to-end demo)
minions plan Demo                    # dry-run by default — no LLM calls, $0
minions plan Demo --no-dry-run       # invoke real Claude Sonnet (~$0.05–$0.15)
minions decisions list               # show pending decisions
minions decisions show <id-prefix>   # full decision detail (4-char prefix is enough)
minions decisions approve <id> [-r "reason"]
minions decisions reject  <id> [-r "reason"]
```

For real planning runs set the API key:
```bash
export ANTHROPIC_API_KEY=sk-ant-...
minions plan Demo --no-dry-run
```

## Observability (Langfuse)

Every CrewAI LLM call auto-traces into Langfuse when credentials are set. You get one trace per crew run (planning / engineer) with child generations for each LLM call — prompt, response, latency, token counts, est. cost — filterable by project / decision_id / dry_run / cadence.

Setup (3 minutes — cloud free tier):

1. Sign up at <https://cloud.langfuse.com>, create a project ("minions").
2. Settings → API Keys → "+ Create new API keys". Copy the public + secret keys.
3. Add to `.env` (the orchestrator auto-loads it on startup):
   ```
   LANGFUSE_PUBLIC_KEY=pk-lf-...
   LANGFUSE_SECRET_KEY=sk-lf-...
   LANGFUSE_HOST=https://cloud.langfuse.com   # or your self-host URL
   ```
4. Verify:
   ```bash
   minions langfuse
   # → Host: https://cloud.langfuse.com
   # → ✓ authenticated to https://cloud.langfuse.com
   ```
5. Run anything with LLM calls: `minions plan Demo --no-dry-run` — your trace appears in real time at `https://cloud.langfuse.com/traces`.

Self-hosting works the same way: `docker compose up` from the [Langfuse repo](https://github.com/langfuse/langfuse) and point `LANGFUSE_HOST` at it.

When credentials aren't set, `@observe_crew` is a true no-op pass-through — no warnings, no overhead.

## Email approvals (Gmail SMTP)

Default notifier is `ConsoleNotifier` — pretty terminal panels. Switch to real Gmail emails with **5 minutes of setup**:

1. Make sure 2-Step Verification is on for your Google account.
2. Generate a Gmail App Password at <https://myaccount.google.com/apppasswords> (label it "minions"). 16 chars, no spaces.
3. Store it:
   ```bash
   export MINIONS_SECRET_GMAIL_APP_PASSWORD="<16-char-password>"
   # OR (production):
   aws secretsmanager create-secret --name minions/gmail-app-password --secret-string "<16-char-password>"
   ```
4. Switch on:
   ```bash
   export MINIONS_NOTIFIER=gmail
   ```
5. Verify:
   ```bash
   minions notify-test     # sends a real test email to the owner address
   ```

The owner address comes from `config/portfolio.yaml.owner` (currently `operator@example.com`). Magic-link tokens are embedded in every email (72h TTL, HMAC-signed) so once the webhook receiver lands, links become clickable. Until then the email shows the exact `minions decisions approve <id>` command to copy/paste.

If `MINIONS_NOTIFIER=gmail` but the secret is missing, the orchestrator falls back to ConsoleNotifier with a clear warning — no silent failures.

## GitHub

The orchestrator has its own scoped GitHub REST client:
- Cannot merge PRs (no method exists in code)
- Refuses to operate on `main`/`master`/`trunk`/`develop` (also enforced server-side via branch protection)
- Defaults all PRs to draft

```bash
minions github check Demo         # repo metadata + first 5 open issues
minions github issues Demo -l mini:idea -n 20   # filter and list issues
```

Token resolution order:
1. `GITHUB_TOKEN` env var
2. AWS Secrets Manager `minions/github-token`
3. `gh auth token` (local-dev convenience — works as long as `gh auth login` was done)

If you have a stale `GITHUB_TOKEN` in your shell, `unset` it (or `export GITHUB_TOKEN=$(gh auth token)`).

## Secrets

Resolution chain (first hit wins):
1. **EnvBackend** — `MINIONS_SECRET_<NAME_UPPER>` env vars (local dev)
2. **AwsSecretsManagerBackend** — `minions/<name>` from AWS Secrets Manager (production)

```bash
minions secrets backends           # list active backends
minions secrets check anthropic-api-key   # verify resolvable, masks the value
```

### AWS Secrets Manager setup (production)

1. Configure AWS credentials however you like (`aws configure`, IAM role on the runtime, env vars). The orchestrator uses standard boto3 resolution.
2. Create an IAM policy granting `secretsmanager:GetSecretValue` scoped to `arn:aws:secretsmanager:*:*:secret:minions/*`. Attach to the role/user the orchestrator runs as.
3. Create the secrets you need:
   ```bash
   aws secretsmanager create-secret --name minions/anthropic-api-key --secret-string sk-ant-...
   aws secretsmanager create-secret --name minions/token-signing-key --secret-string "$(openssl rand -hex 32)"
   aws secretsmanager create-secret --name minions/github-app-private-key --secret-string "$(cat key.pem)"
   ```
4. Verify: `minions secrets check anthropic-api-key` should print a masked value.

The AWS backend falls through to "not found" silently on missing creds / wrong region / non-existent secret — so dev environments without AWS still work via env vars. **AccessDenied propagates** so misconfigured IAM policies get noticed.

`get_token_signing_key()` has a dev-only fallback that catches *all* errors (including AccessDenied) so the planning flow can run even when AWS is misconfigured. Production deploys MUST set `MINIONS_TOKEN_SECRET` or grant proper IAM access.

### Run tests

```bash
pytest
```

### Lint / typecheck

```bash
ruff check src tests
ruff format src tests
mypy src
```

## Day-1 operator checklist (before any agent runs)

These need your hands at the keyboard for OAuth / console clicks; they cannot be automated.

1. **Confirm demo_four repo URL** — replace `TBD` in `projects/demo_four.yaml`.
2. **Create GitHub App `minions-org`** — scopes:
   - `contents: write` (for feature branches only)
   - `pull-requests: write`
   - `issues: write`
   - **NO** `secrets: read`, **NO** `administration`, **NO** `members`
   Install on the 4 GitHub repos.
3. **Branch protection** on `main` for Demo, demo_two, demo_three, demo_four:
   - Require PR
   - Require ≥1 review
   - Require status checks
   - No direct push, no admin bypass
4. **AWS Secrets Manager** — provision a secrets manager instance in your AWS account; create one secret per integration (`minions/anthropic-api-key`, `minions/github-app-private-key`, `minions/gmail-oauth`, `minions/neon-dsn`, `minions/langfuse`). Create an IAM role/user the orchestrator process will assume.
5. **Anthropic API key** — store in AWS Secrets Manager.
6. **Neon Postgres** — create a project (`minions-audit`); run schema migration (Phase 1.6 will provide).
7. **Gmail** — set up OAuth app for the orchestrator with `gmail.send` and `gmail.modify` scopes; create label `minions/approval`.
8. **Langfuse** — sign up (cloud free tier OK) or self-host; store project key in AWS Secrets Manager.
9. **Runtime host** — pick Modal or Fly.io; wire in deps; deploy a smoke test.

After Day-1, Phase 2.x can wire up the actual CrewAI crews and start producing Decision Records (still local until Phase 3 lights up Gmail + Neon).

## Hard safety rules (encoded in 4 layers)

1. **Prompt** — every agent's system prompt contains the four hard rules.
2. **Tooling** — filesystem deny-list for `.env*`, `secrets/**`, `**/*.pem`, `**/*.key`, `**/credentials*`. Git tool refuses pushes to `main`/`master`. PR tool can open and comment but not merge.
3. **Platform** — GitHub branch protection. App scope excludes secrets read.
4. **Network** — runtime egress allowlist: Anthropic, GitHub, Gmail, Langfuse, Neon, AWS Secrets Manager only.

Plus a 90-day audit log in Langfuse + Neon, replayable by Decision Record id.

## Project layout

```
minions/
├── config/
│   └── portfolio.yaml          # portfolio-level config (cadence, audit, procurement, etc.)
├── projects/
│   ├── Demo.yaml               # active manifests
│   ├── demo_two.yaml
│   ├── demo_four.yaml
│   ├── demo_three.yaml
│   └── _deferred/
│       └── trading.yaml        # not active until re-instated
├── openspec/                   # full design + phased plan
│   ├── project.md
│   └── changes/bootstrap-agent-org/
├── src/minions/
│   ├── __main__.py             # CLI
│   ├── agents/
│   │   ├── base.py             # MinionAgent
│   │   └── safety.py           # safety preamble (the four hard rules)
│   ├── config/
│   │   └── portfolio.py        # PortfolioConfig
│   └── models/
│       ├── audit.py            # AuditFinding
│       ├── decision.py         # Decision Record
│       ├── manifest.py         # Manifest
│       └── roles.py            # Role enum + tier mapping
└── tests/
```

## Cost discipline (v0)

Every model call passes through middleware (Phase 6.1) that records (project, role, decision-id, model, tokens, cost) and aggregates nightly. Per-project weekly cap → throttle → operator notify. Total target $14/mo across 4 active projects.

## License

Proprietary — internal use only.
