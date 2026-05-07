# Security Policy

## Reporting a vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.**

Email the maintainers privately and we'll respond within 5 business days. If you don't hear back, please escalate via GitHub Discussions (without disclosing the bug).

If the vulnerability is in a dependency rather than minions itself, please also report it upstream.

## Scope

In-scope for security reports:

- Anything that lets an agent or PR-author read/exfiltrate secrets, `.env`, or AWS credentials.
- Anything that bypasses the four safety rules in `src/minions/agents/safety.py`.
- Anything that lets an agent push to `main`/`master`/`trunk`/`develop`, merge a PR, or modify branch protection.
- Token forgery against `src/minions/approval/tokens.py` (HMAC bypass).
- Replay attacks against the magic-link webhook.
- CI workflows that expose repository secrets to PRs from forks.

Out of scope:

- Vulnerabilities in upstream packages (report those upstream).
- Issues that require physical access to the operator's machine.
- Social engineering of the operator.

## Hardening guidance for self-hosters

If you deploy minions to manage your own portfolio:

1. Set a real `MINIONS_TOKEN_SECRET` (32+ bytes from `secrets.token_urlsafe`). Never use the dev fallback in production — it warns loudly and signs verifiable but trivially-forgeable tokens.
2. Set GitHub Actions workflow permissions to read-only by default (Settings → Actions → Workflow permissions).
3. Enable secret scanning + push protection on your fork.
4. Require approval for first-time contributors before workflows run on PRs (Settings → Actions).
5. Never pass secrets to workflows triggered by `pull_request_target` if any step checks out PR code.
6. Review `agents/safety.py` after every dependency upgrade — the safety preamble is appended to every system prompt and we never want it accidentally truncated or unwrapped.

## Disclosure

We aim to publish a CVE and patch within 30 days of a confirmed report. Reporters will be credited (or remain anonymous, by request).
