# Operator setup guide

This guide walks you from a clean clone to a working autonomous engineering org over your portfolio of GitHub repos. Total time: ~30–45 minutes.

The setup is **modular**. Every external service (Anthropic, Postgres, Gmail, Langfuse, AWS, Fly.io) is optional in dev; add them as you need them.

## Layer 0 — local dev (no external services, 5 minutes)

```bash
git clone https://github.com/sagacioussid02/minions
cd minions
uv pip install -e ".[dev]"
pytest                              # all tests should pass
minions check                       # validates the demo portfolio
minions org                         # prints org topology
minions cron weekly                 # dry-run sweep — no LLM calls, no money
```

Stores fall back to JSON files in `data/local/`. Notifier falls back to console output. This is enough to develop on the codebase end-to-end.

## Layer 1 — your first real planning run (~5 minutes, $0.10)

Add an Anthropic API key:

```bash
cp .env.example .env
# Edit .env, set ANTHROPIC_API_KEY
minions anthropic                   # verifies the key works
```

Add your first project. Create `projects/myapp.yaml` (use `projects/demo.yaml` as a template) and edit:

```yaml
name: myapp
source:
  kind: github
  path: /absolute/path/to/your/repo
  repo: your-org/myapp
  default_branch: main
weekly_budget_usd: 1.00
monthly_budget_usd: 4.00
delivery_targets:
  scope: portfolio
  share_weight: 0.5
```

Run a real planning sweep for that project:

```bash
minions plan myapp --no-dry-run
minions decisions list              # see the proposed sprint
```

A Decision Record now sits in the local JSON store. Approve or reject it:

```bash
minions decisions show <id-prefix>
minions decisions approve <id-prefix>
```

That's the full closed loop. Everything from here adds robustness or convenience.

## Layer 2 — Postgres (Neon free tier, 5 minutes)

For multi-machine deployments and persistent state across restarts, swap JSON for Postgres.

1. Sign up at [neon.tech](https://neon.tech), create a project, copy the connection string.
2. Add to `.env`:
   ```
   MINIONS_DATABASE_URL=postgresql://user:pass@host/db?sslmode=require
   ```
3. Run migrations:
   ```bash
   minions db migrate
   minions db status
   ```
4. (Optional) Backfill existing JSON data into Postgres:
   ```bash
   minions db backfill
   ```

The next `minions decisions list` will read from Postgres. Code paths use `make_decision_store()` so this is transparent.

## Layer 3 — Gmail email approvals (~10 minutes)

Get clickable approve/reject buttons in your inbox.

1. Enable 2-Step Verification on the Gmail account that owns your portfolio.
2. Create a Gmail App Password at [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords). Label it "minions".
3. Add to `.env`:
   ```
   MINIONS_NOTIFIER=gmail
   MINIONS_SECRET_GMAIL_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx
   ```
4. Update the `owner:` field in `config/portfolio.yaml` to your Gmail address.
5. Verify:
   ```bash
   minions notify-test                 # sends a real email
   ```

Every `submit_for_approval()` now mails you. Without the webhook (Layer 4), the email shows CLI commands + raw HMAC tokens.

## Layer 4 — Fly.io webhook for one-click approvals (~15 minutes)

```bash
brew install flyctl
fly auth login
python -c "import secrets; print(secrets.token_urlsafe(48))" > /tmp/k
echo "MINIONS_TOKEN_SECRET=$(cat /tmp/k)" >> .env
echo "MINIONS_WEBHOOK_BASE_URL=https://YOUR-APP.fly.dev" >> .env

fly launch --no-deploy --copy-config --name YOUR-APP -c infra/fly/webhook/fly.toml
fly secrets set \
  MINIONS_DATABASE_URL="$(grep ^MINIONS_DATABASE_URL .env | cut -d= -f2-)" \
  MINIONS_TOKEN_SECRET="$(cat /tmp/k)" \
  -a YOUR-APP --stage
fly deploy . -c infra/fly/webhook/fly.toml --dockerfile infra/fly/webhook/Dockerfile
curl https://YOUR-APP.fly.dev/healthz       # → {"status":"ok"}
rm /tmp/k
```

Approval emails sent after this point will contain real Approve/Reject buttons. `MINIONS_TOKEN_SECRET` must be byte-identical between your orchestrator's `.env` and Fly's secrets — otherwise every link fails signature verification.

## Layer 5 — observability (Langfuse, optional)

```bash
# Sign up at langfuse.com, create a project, copy keys.
echo "LANGFUSE_PUBLIC_KEY=pk-lf-..." >> .env
echo "LANGFUSE_SECRET_KEY=sk-lf-..." >> .env
minions langfuse                       # verifies auth
```

Every crew run will now appear as a trace in Langfuse with full prompt/response visibility.

## Layer 6 — scheduled cron (GitHub Actions)

Workflows are not shipped in the OSS template (they require your specific secrets). Roll your own using the `minions cron weekly|daily|friday` entrypoints. A minimal example:

```yaml
# .github/workflows/weekly.yml
name: Weekly planning
on:
  schedule: [{ cron: "0 14 * * 1" }]   # Monday 09:00 ET
  workflow_dispatch:
permissions: { contents: read }
jobs:
  plan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - run: uv pip install -e ".[dev]"
      - run: minions cron weekly --no-dry-run
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          MINIONS_DATABASE_URL: ${{ secrets.MINIONS_DATABASE_URL }}
          MINIONS_TOKEN_SECRET: ${{ secrets.MINIONS_TOKEN_SECRET }}
          MINIONS_WEBHOOK_BASE_URL: ${{ secrets.MINIONS_WEBHOOK_BASE_URL }}
          MINIONS_NOTIFIER: gmail
          MINIONS_SECRET_GMAIL_APP_PASSWORD: ${{ secrets.GMAIL_APP_PASSWORD }}
```

Scope each secret to the workflows that need it. **Never** trigger this kind of workflow on `pull_request_target` — it would expose your secrets to PRs from forks.

## Troubleshooting

- `Anthropic preflight failed: 401` → key is wrong/expired. Regenerate at console.anthropic.com.
- `MINIONS_DATABASE_URL not retrievable` → check the URL has `?sslmode=require`.
- Webhook returns `400 bad signature` → token secret mismatch between orchestrator and Fly. Re-set Fly secret to match `.env`.
- `gh auth token` fallback warnings → either set `GITHUB_TOKEN` to a valid PAT in `.env` or `unset GITHUB_TOKEN`.
- Streamlit dashboard renders blank → hard-refresh the browser tab; check the terminal for a stack trace.
