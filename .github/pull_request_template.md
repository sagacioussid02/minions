## Summary

<!-- What does this PR change, and why? -->

## Linked issues

<!-- e.g. Closes #123, Refs #456 -->

## Type of change

- [ ] Bug fix (non-breaking)
- [ ] New feature (non-breaking)
- [ ] Breaking change
- [ ] Documentation
- [ ] Refactor / cleanup

## Checklist

- [ ] Tests added or updated (and passing locally)
- [ ] `ruff check src tests` passes
- [ ] `mypy src` passes
- [ ] No new dependencies, OR rationale explained below
- [ ] No changes to safety-critical files (see list below), OR explicit rationale + maintainer review requested

## Safety-critical files touched?

The following files require explicit maintainer review. Tick if your PR touches any:

- [ ] `src/minions/agents/safety.py`
- [ ] `src/minions/github/client.py`
- [ ] `src/minions/secrets.py`
- [ ] `src/minions/approval/tokens.py`
- [ ] `src/minions/webhook/app.py`
- [ ] `.github/workflows/`

If yes, please describe the threat-model implications:

<!-- e.g. "Tightens the branch refusal list to also block release/* branches.
     Does not weaken any existing guarantee." -->

## Test plan

<!-- How did you verify this works? Commands run, manual steps, screenshots if UI. -->
