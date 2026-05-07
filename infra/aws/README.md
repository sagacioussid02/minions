# AWS infra setup

## IAM policy for the orchestrator identity

Attach `minions-secrets-policy.json` to whatever identity the orchestrator
runs as — for local dev this is your IAM user (e.g. `aiengineer`); for
production this is the IAM role attached to the runtime (Modal, Fly.io,
Lambda, etc.).

The policy grants:

| Action | Resource | Purpose |
|---|---|---|
| `CreateSecret`, `PutSecretValue`, `UpdateSecret`, `DeleteSecret`, `DescribeSecret`, `GetSecretValue`, `TagResource`, `UntagResource` | `arn:aws:secretsmanager:*:*:secret:minions/*` | Manage secrets under the `minions/` namespace only |
| `ListSecrets` | `*` | AWS doesn't support resource-level scoping on `ListSecrets`. Discovery only — no value access. |

`CreateSecret` is resource-restricted (`minions/*`), so the user can only
create secrets within the `minions/` namespace. Wildcards match the trailing
random suffix AWS appends to secret ARNs.

## Apply (dev — IAM user)

```bash
# 1) Sanity-check current attachments
aws iam list-attached-user-policies --user-name aiengineer
aws iam list-user-policies          --user-name aiengineer

# 2a) Attach as a managed policy (preferred — reusable across users/roles)
aws iam create-policy \
  --policy-name MinionsSecretsAccess \
  --policy-document file://infra/aws/minions-secrets-policy.json

# Capture the ARN this prints, then:
aws iam attach-user-policy \
  --user-name aiengineer \
  --policy-arn arn:aws:iam::662246314589:policy/MinionsSecretsAccess

# 2b) OR attach inline (faster, less reusable)
aws iam put-user-policy \
  --user-name aiengineer \
  --policy-name MinionsSecretsAccess \
  --policy-document file://infra/aws/minions-secrets-policy.json

# 3) Verify
aws iam simulate-principal-policy \
  --policy-source-arn arn:aws:iam::662246314589:user/aiengineer \
  --action-names secretsmanager:CreateSecret secretsmanager:GetSecretValue \
  --resource-arns "arn:aws:secretsmanager:*:*:secret:minions/anthropic-api-key"
```

The `simulate-principal-policy` call should print `EvaluationResults` with
`EvalDecision: allowed` for both actions.

## Bootstrap the secrets

```bash
aws secretsmanager create-secret \
  --name minions/anthropic-api-key \
  --description "Anthropic API key for the minions org" \
  --secret-string "sk-ant-..."

aws secretsmanager create-secret \
  --name minions/token-signing-key \
  --description "HMAC key for approval magic-link tokens" \
  --secret-string "$(openssl rand -hex 32)"

# Add as you wire up each integration:
# aws secretsmanager create-secret --name minions/github-app-private-key --secret-string "$(cat key.pem)"
# aws secretsmanager create-secret --name minions/gmail-oauth            --secret-string "{...}"
# aws secretsmanager create-secret --name minions/neon-dsn               --secret-string "postgres://..."
# aws secretsmanager create-secret --name minions/langfuse               --secret-string "{...}"
```

## Verify from the orchestrator

```bash
minions secrets check anthropic-api-key
# → ✓ anthropic-api-key resolved (masked: sk-a…XYZ, length: 108)
```

## Production: switch to an IAM role

When deploying to Modal / Fly.io / Lambda, attach the same policy to the
runtime's IAM role instead of a user. Local dev keeps the user; production
uses role assumption. The orchestrator code is identical — boto3's default
credential chain handles both.
