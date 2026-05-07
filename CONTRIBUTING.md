# Contributing to minions

Thanks for your interest. This project lives or dies by its safety guarantees, so contributions in the safety-critical layers go through extra review — but everything else is open game and we'd love help.

## Quick setup

```bash
git clone https://github.com/sagacioussid02/minions
cd minions
uv pip install -e ".[dev]"        # or: pip install -e ".[dev]"
pre-commit install                 # optional but recommended

# Verify your environment:
pytest                             # ~3 seconds, all tests should pass
ruff check src tests
mypy src
```

If `pytest` fails on a clean clone, that's a bug — please open an issue.

## Local-only development (no API keys)

The default JSON backends + `ConsoleNotifier` mean you can iterate on most of the codebase without any external services:

```bash
minions check                          # validates config and manifests
minions org                            # prints org topology
minions plan Demo                      # dry-run by default — prints what it would do
minions decisions list                 # inspect submitted Decisions
minions dashboard                      # Streamlit UI at http://localhost:8501
```

The crew tests use mocked Claude responses (see `tests/test_planning_crew.py`), so you can develop and test the planning flow with no Anthropic key at all.

## What we want help with

Look at [issues labeled `good first issue`](https://github.com/sagacioussid02/minions/labels/good%20first%20issue). Common contribution shapes:

- **CLI commands & flags** — small, well-scoped, lots of room.
- **Dashboard polish** — Streamlit views in `src/minions/dashboard/`. Plenty of UX issues.
- **Engineer crew** — the next major milestone. See `src/minions/crews/engineer.py` and the issues tagged `engineer-crew`.
- **Docs & examples** — always welcome.
- **Bug fixes** — most carry tests; please add a regression test with the fix.

## Coding standards

- **Python 3.12+**, strict typing.
- `ruff check src tests` and `ruff format src tests` — CI enforces.
- `mypy src` strict mode — CI enforces. Add new untyped third-party deps to the `[[tool.mypy.overrides]]` block in `pyproject.toml` rather than weakening strictness.
- `pytest` — every PR should add or update tests. Aim for the test name to read like a sentence describing the behavior.
- Keep PRs small and focused. One logical change per PR.

## Safety-critical files

These files encode the contract between agents and the operator. Changes require **explicit maintainer review** and a clear rationale in the PR description:

- `src/minions/agents/safety.py` — the four hard rules
- `src/minions/github/client.py` — branch refusal + no-merge enforcement
- `src/minions/secrets.py` — secret resolution chain
- `src/minions/approval/tokens.py` — HMAC token signing/verification
- `src/minions/webhook/app.py` — magic-link handler
- `.github/workflows/` — CI permissions + secret exposure

`CODEOWNERS` will route these PRs to maintainers automatically. Please open an issue **before** the PR if you want to change behavior in any of them.

## What we won't merge

- Changes that weaken any of the four safety rules in `safety.py` without a documented threat-model update.
- Workflows that use `pull_request_target` with PR code checked out (this exposes secrets to forks).
- Code that reads `.env` from agent-side execution paths.
- New dependencies without a clear rationale (we keep the surface small).
- Auto-merge configurations.
- Changes to `branch_refusal` in the GitHub client (the list of `main`/`master`/`trunk`/`develop` is intentionally hardcoded).

## Commit / PR style

- Commit messages: short imperative subject, body explains the *why* if non-obvious.
- PR description: state what changed and why, link the issue, mention any safety-critical files touched.
- Include test output / screenshots where useful.
- Sign your commits if you can (`git commit -S`). Not required.

## Releasing

Maintainers only — see internal notes. Contributors don't need to think about this.

## Reporting security issues

Please **don't** open public issues for security bugs. See [`SECURITY.md`](SECURITY.md).

## Code of Conduct

By participating, you agree to abide by the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md).

---

Thanks again. The most useful thing you can do as a new contributor is install it, try to use it, and tell us where it's confusing — those issues are gold.
