# Launch checklist

Steps to flip the repo public. Most can only be done via the GitHub web UI or after the repo is already public, so they're left for you.

## Before flipping public

- [ ] Verify CI is green on `main` (the workflow runs on push).
- [ ] Skim the auto-generated `good first issue` tickets; close any that don't apply.
- [ ] Optional: write a launch post / tweet draft. The hook is the four-layer safety model.

## Flip to public

GitHub: **Settings → General → scroll to "Danger Zone" → Change visibility → Make public.**

## Immediately after flipping public

These features unlock on free public repos but are off by default — turn them all on.

- [ ] **Settings → Code security and analysis**
  - Enable "Secret scanning"
  - Enable "Push protection"
  - Enable "Dependabot alerts"
  - Enable "Dependabot security updates"
  - Enable "Code scanning" (CodeQL, free for public repos)

- [ ] **Settings → Branches → Add branch protection rule for `main`:**
  - Require a pull request before merging
  - Require approvals: 1
  - Dismiss stale pull request approvals when new commits are pushed
  - Require review from Code Owners
  - Require status checks: `tests + lint + types` (the CI job name)
  - Require branches to be up to date before merging
  - Do not allow bypassing the above settings

- [ ] **Settings → Actions → General:**
  - "Fork pull request workflows from outside collaborators": **"Require approval for first-time contributors"** (or stricter)
  - "Workflow permissions": Read repository contents and packages permissions (already set via API)
  - Disallow GitHub Actions to create pull requests (unless you specifically want this)

- [ ] **Settings → General:**
  - "Default branch": confirm it's `main`
  - "Pull Requests" section: enable squash merging only; disallow merge commits and rebase merging (cleaner history)
  - "Automatically delete head branches": enabled
  - Disable wikis (we use docs/ instead)

- [ ] **Settings → Discussions** is already on. Pin a "Welcome / Roadmap" post.

## Optional polish

- [ ] Add a social preview image (Settings → General → Social preview).
- [ ] Add repo topics: `ai`, `agents`, `autonomous-agents`, `crewai`, `claude`, `llm`, `python`.
- [ ] Set up GitHub Sponsors if you want.

## After launch — week 1

- [ ] Triage every issue within 48h, even if just to label it.
- [ ] Don't merge any PR you don't fully understand.
- [ ] Reject (politely) any PR that weakens safety rules without a real-world rationale.
- [ ] Watch GitHub's "Insights → Traffic" to see referrer sources.
