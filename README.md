# minions

> An autonomous AI engineering organization for managing a portfolio of software projects — with hard human-in-the-loop approval gates on every meaningful action.

[![CI](https://github.com/sagacioussid02/minions/actions/workflows/ci.yml/badge.svg)](https://github.com/sagacioussid02/minions/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/)

> **Status: alpha, accepting contributions.** Browse [`good first issue`](https://github.com/sagacioussid02/minions/labels/good%20first%20issue) to start.

`minions` runs a virtual engineering organization (Product Owners, Principals, Managers, Engineers, Auditors, Devil's Advocates) over your portfolio of GitHub repos. Each "agent" is a [CrewAI](https://github.com/joaomdmoura/crewAI) actor backed by a tier-appropriate Claude model (Opus / Sonnet / Haiku). The org plans sprints, opens PRs, audits its own work — but it can never merge to `main`, never read your `.env`, and never spend a dollar without your explicit approval.

The whole system is built around **Decision Records**: a durable artifact every agent must produce when proposing anything non-trivial, gated by an approval queue that the operator (you) resolves via email magic-link, CLI, or Streamlit dashboard.

---

## Why this might be interesting

Most "AI agent" projects optimize for autonomy. This one optimizes for **defensible autonomy** — letting agents do real work while encoding hard guarantees that they can't go rogue:

- **Layer 1 — prompt:** every agent has a non-negotiable safety preamble: *no .env reads, no main commits, always branch + PR + review, every meaningful action through a Decision Record + approval gate*.
- **Layer 2 — tooling deny-list:** the in-process GitHub client has no `merge` method and refuses to push to `main`/`master`/`trunk`/`develop`. PRs default to draft.
- **Layer 3 — branch protection:** the operator's GitHub repo enforces required reviews on `main`.
- **Layer 4 — egress allowlist:** runtime sandbox restricts what the process can reach.

Approvals flow through HMAC-signed magic links (72h TTL) embedded in email — clicking lands at a tiny FastAPI receiver that verifies the token and updates Postgres. CLI and a Streamlit operator dashboard are equivalent surfaces over the same store.

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the full design.

---

## Quickstart (60 seconds, no API keys needed)

```bash
git clone https://github.com/sagacioussid02/minions
cd minions
uv pip install -e ".[dev]"            # or: pip install -e ".[dev]"

# Validate the shipped example portfolio + manifests:
minions check

# Print the org topology (no LLM calls, no money):
minions org

# Dry-run the weekly planner (still no LLM calls):
minions cron weekly                    # default is --dry-run
```

You should see five demo projects (`Demo`, `demo_two`, …, `demo_five`) loaded, agents resolved per project, and a clean planning sweep that submits stub Decisions to the local JSON store.

To make it do real work, copy `.env.example` to `.env`, fill in an Anthropic API key, and add your own project under `projects/`. See [`docs/SETUP.md`](docs/SETUP.md) for the full operator path (Anthropic, Gmail App Password, Neon Postgres, optional Fly.io webhook deployment).

---

## What's in the box

| Surface | How to reach it |
|---|---|
| CLI | `minions --help` (typer-based) |
| Operator dashboard | `minions dashboard` (Streamlit, http://localhost:8501) |
| Approval webhook | `minions.webhook.app:app` (FastAPI, deployable to Fly.io) |
| Cron entrypoints | `minions cron weekly\|daily\|friday` |

Stores are dual-backend: pure JSON locally so you can hack on the project with no database, Postgres (Neon-compatible) when you set `MINIONS_DATABASE_URL`. Tests cover both backends.

---

## Status

This project is **alpha**. The planning crew, approval graph, dashboard, and webhook are all functional and well-tested (346 passing tests, mypy strict, ruff clean). The Engineer crew that converts approved Decisions into actual PRs is **in progress** — that's where most of the open issues live, and is a great place to contribute.

---

## Contributing

We'd love your help. See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the development setup, coding standards, and PR process. The [`good first issue`](https://github.com/sagacioussid02/minions/labels/good%20first%20issue) label is a curated entry point.

Before opening a PR that touches any of these files, please open an issue first to discuss — they're load-bearing for safety:

- `src/minions/agents/safety.py`
- `src/minions/github/client.py`
- `src/minions/secrets.py`
- `src/minions/approval/tokens.py`
- `.github/workflows/`

Security issues: please email rather than filing a public issue. See [`SECURITY.md`](SECURITY.md).

---

## License

MIT — see [`LICENSE`](LICENSE).
