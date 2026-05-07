# minions approval webhook (Fly.io)

Tiny FastAPI app that turns the HMAC-signed magic links in approval emails
into one-click approvals. Code lives at `src/minions/webhook/`.

## First-time deploy

```bash
# from repo root
fly launch --no-deploy --copy-config -c infra/fly/webhook/fly.toml --name minions-webhook
fly secrets set \
  MINIONS_DATABASE_URL="$(grep ^MINIONS_DATABASE_URL .env | cut -d= -f2-)" \
  MINIONS_TOKEN_SECRET="$(grep ^MINIONS_TOKEN_SECRET .env | cut -d= -f2-)" \
  -a minions-webhook
fly deploy -c infra/fly/webhook/fly.toml
```

The `MINIONS_TOKEN_SECRET` value MUST be the same one the orchestrator uses
to sign tokens — otherwise every link will fail signature verification.

## Wire emails to the deployed URL

Set `MINIONS_WEBHOOK_BASE_URL=https://minions-webhook.fly.dev` in the
orchestrator's `.env` (and CI / scheduled-job env). Emails sent after that
will include real approve/reject buttons instead of CLI commands + raw
tokens.

## Cost

`auto_stop_machines = "stop"` + `min_machines_running = 0` means the VM
sleeps when idle and cold-starts on the first request — typically <1s.
At our request volume this is effectively $0/mo.

## Operate

```bash
fly logs -a minions-webhook
fly status -a minions-webhook
curl https://minions-webhook.fly.dev/healthz   # → {"status":"ok"}
```
