"""GitHub token resolver.

Resolution order:
  1. ``GITHUB_TOKEN`` env var (standard; used by CI and explicit overrides)
  2. AWS Secrets Manager: ``minions/github-token`` (production source of truth)
  3. ``gh auth token`` from the GitHub CLI (local-dev convenience fallback)

If you have a stale ``GITHUB_TOKEN`` set in your shell, the chain stops at step 1
and you'll get a 401. Either unset it or refresh:
``export GITHUB_TOKEN=$(gh auth token)``.

Production seam: a GitHub App installation token. To wire that up later:
  1. Resolve the App's private key from secret ``minions/github-app-private-key``
     and the App ID from ``minions/github-app-id``.
  2. Sign a 10-minute JWT with the private key (RS256).
  3. POST /app/installations/<id>/access_tokens with that JWT to get a
     short-lived installation token (60 min TTL).
  4. Cache the installation token until close to expiry.
This module is the only place that needs to change to swap PAT → App auth.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess

import httpx

from minions.secrets import SecretNotFound, get_secret

logger = logging.getLogger(__name__)


# Per-process cache: token → (is_valid, when_checked).
# Keeps the validation cost to one HTTP call per token per process.
_validation_cache: dict[str, bool] = {}


def get_github_token(*, validate: bool = True) -> str:
    """Resolve the GitHub token used by the orchestrator.

    With ``validate=True`` (default), the env-var token is verified against
    ``GET /user`` once per process — if it 401s we log a warning and fall
    through to the next source instead of dead-ending. Tests pass
    ``validate=False`` to skip the network call.
    """
    direct = os.environ.get("GITHUB_TOKEN")
    if direct:
        if not validate or _is_token_valid(direct):
            return direct
        logger.warning(
            "GITHUB_TOKEN env var is set but rejected by GitHub (401). "
            "Falling through to AWS Secrets Manager / `gh auth token`. "
            "Run `unset GITHUB_TOKEN` (or `export GITHUB_TOKEN=$(gh auth token)`) "
            "to silence this."
        )

    try:
        return get_secret("github-token")
    except SecretNotFound:
        pass

    via_gh = _try_gh_cli()
    if via_gh:
        return via_gh

    raise SecretNotFound(
        "GitHub token not found. Tried GITHUB_TOKEN env var, AWS Secrets "
        "Manager ('minions/github-token'), and `gh auth token`. "
        "For dev: install + sign in with `gh auth login`. "
        "For production: create the AWS secret."
    )


def _is_token_valid(token: str, *, timeout: float = 3.0) -> bool:
    """One-shot validation against ``GET /user``. Cached per-token-per-process."""
    if token in _validation_cache:
        return _validation_cache[token]
    try:
        r = httpx.get(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=timeout,
        )
        ok = r.status_code == 200
    except (httpx.HTTPError, OSError) as e:
        # Network blip — don't pretend the token is bad. Trust + retry later.
        logger.debug("GitHub token validation network error: %s", e)
        ok = True
    _validation_cache[token] = ok
    return ok


def reset_validation_cache() -> None:
    """Clear the per-process validation cache. Test-only."""
    _validation_cache.clear()


def _try_gh_cli() -> str | None:
    """Read the GitHub token from ``gh auth token`` if the CLI is signed in.

    We scrub ``GITHUB_TOKEN`` from the subprocess env because ``gh`` will
    happily echo the env-var token if it's set — and we only fall through
    to this path *because* the env-var token was rejected. We want the
    keyring token instead.
    """
    if not shutil.which("gh"):
        return None
    env = {k: v for k, v in os.environ.items() if k not in ("GITHUB_TOKEN", "GH_TOKEN")}
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            env=env,
        )
    except (subprocess.SubprocessError, OSError) as e:
        logger.debug("gh auth token invocation failed: %s", e)
        return None
    if result.returncode != 0:
        logger.debug("gh auth token returned %d: %s", result.returncode, result.stderr.strip())
        return None
    token = result.stdout.strip()
    return token or None
