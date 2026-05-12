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

## Install

The package is published to PyPI as **`minionscli`**. The CLI binary is `minions`.

### As an end-user (just want to run it)

```bash
# Recommended: isolated install with uv (https://github.com/astral-sh/uv)
uv tool install minionscli
# or with pipx
pipx install minionscli
# or plain pip into a venv
pip install minionscli
```

After install, the `minions` command is on your PATH. Verify:

```bash
minions --help
minions check
```

### Bleeding edge from git (pre-PyPI release or unreleased fixes)

```bash
uv tool install git+https://github.com/sagacioussid02/minions
# or
pipx install git+https://github.com/sagacioussid02/minions
```

### As a contributor (editable install with tests)

```bash
git clone https://github.com/sagacioussid02/minions
cd minions
uv pip install -e ".[dev]"   # or: pip install -e ".[dev]"
pytest                        # ~3 seconds, all tests should pass
```

### Pre-built wheels (offline / air-gapped)

Each tagged release attaches a built wheel + sdist to the GitHub Releases page. Download from https://github.com/sagacioussid02/minions/releases and:

```bash
pip install ./minionscli-*.whl
```

## Quickstart (60 seconds, no API keys needed)

After install:

```bash
# 1. Validate the shipped example portfolio + manifests
minions check

# 2. Print the org topology (no LLM calls, no money)
minions org

# 3. Dry-run the weekly planner (still no LLM calls)
minions cron weekly                    # default is --dry-run

# 4. Inspect what the planner submitted
minions decisions list

# 5. Open the operator dashboard
minions dashboard                      # → http://localhost:8501
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

The dashboard now ships five pages — **🤖 Agents**, **📡 Activity** (live timeline + guardrails strip), **📋 Decisions**, **📊 Sprint Board**, **🛡️ Audit** — so contributors can *see* the org work without standing up Langfuse. For a full visual tour of every role, when it activates, and what it produces, read [`docs/AGENTS.md`](docs/AGENTS.md).

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
