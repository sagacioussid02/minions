"""Resolve a project's working tree from its manifest.

The orchestrator profiles each managed project by reading files in its working
tree (`tasks.md`, README, source files for TODO scans, recent commits via
``git log``). On the operator's laptop, ``manifest.source.path`` points at a
real local clone. On CI runners that path doesn't exist — so we fall back to
a shallow git clone of ``manifest.source.repo`` into a cache dir and use
that.

This module is the one place that decides:

* If ``source.path`` is set and refers to an existing directory, use it.
* Otherwise, clone ``source.repo`` to ``<cache_dir>/<manifest.name>`` and
  return that path. If the clone exists from a prior run, fetch + reset to
  the latest ``default_branch`` instead of re-cloning from scratch.

Authentication uses :func:`minions.github.auth.get_github_token`, which
itself falls back through ``GITHUB_TOKEN`` env → AWS Secrets Manager → ``gh
auth token``. Public repos work without a token; private repos need one.
"""

from __future__ import annotations

import logging
import shlex
import subprocess
from pathlib import Path

from minions.models.manifest import Manifest

logger = logging.getLogger(__name__)


class WorkingTreeError(RuntimeError):
    """Raised when a manifest's working tree can't be resolved."""


def resolve_working_tree(
    manifest: Manifest,
    *,
    cache_dir: Path,
    token: str | None = None,
) -> Path:
    """Return a real local directory for ``manifest``'s working tree.

    Local-path-first: if ``manifest.source.path`` is set and exists, just
    return it. Otherwise fall back to a shallow git clone of
    ``manifest.source.repo`` into ``cache_dir / manifest.name``.

    ``token`` is used to authenticate clones of private repos. If ``None``,
    we resolve via :func:`minions.github.auth.get_github_token` (env var →
    AWS Secrets Manager → ``gh auth token``). Public-repo clones still work
    when no token is available.
    """
    src = manifest.source

    if src.path:
        local = Path(src.path).expanduser()
        if local.is_dir():
            return local
        logger.info(
            "manifest %s: source.path %s does not exist on this host; "
            "falling back to clone of %s",
            manifest.name,
            local,
            src.repo,
        )

    if not src.repo:
        raise WorkingTreeError(
            f"manifest {manifest.name!r}: source.path is unavailable "
            f"({src.path!r}) and source.repo is not set — cannot resolve "
            "a working tree. Set one of them."
        )

    if token is None:
        token = _resolve_token_safe()

    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / manifest.name
    branch = src.default_branch
    url = _clone_url(src.repo, token)

    if (target / ".git").is_dir():
        _run(["git", "-C", str(target), "fetch", "--depth=1", "origin", branch])
        _run(["git", "-C", str(target), "reset", "--hard", f"origin/{branch}"])
        # In case the default branch changed name upstream, point HEAD at the
        # newly-fetched ref so further `git log` calls work.
        _run(["git", "-C", str(target), "checkout", branch], check=False)
    else:
        _run(
            [
                "git",
                "clone",
                "--depth=1",
                "--single-branch",
                "--branch",
                branch,
                url,
                str(target),
            ]
        )

    return target


# --- internals -------------------------------------------------------------


def _clone_url(repo: str, token: str | None) -> str:
    """Return an HTTPS clone URL, with token embedded if provided.

    GitHub accepts ``https://x-access-token:<token>@github.com/...`` for both
    classic PATs and ``gh auth token`` output.
    """
    base = f"github.com/{repo}.git"
    if token:
        return f"https://x-access-token:{token}@{base}"
    return f"https://{base}"


def _run(cmd: list[str], *, check: bool = True) -> None:
    """Run a subprocess; raise WorkingTreeError on non-zero unless ``check=False``."""
    try:
        subprocess.run(cmd, check=check, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        # Strip token from logged command if present.
        sanitized = [shlex.quote(_redact(a)) for a in cmd]
        raise WorkingTreeError(
            f"git command failed: {' '.join(sanitized)}\nstderr: {e.stderr.strip()}"
        ) from e


def _redact(arg: str) -> str:
    if "x-access-token:" in arg:
        # Replace the token portion with <redacted>, keep the host+path.
        tail = arg.split("@", 1)[1] if "@" in arg else arg
        return f"https://x-access-token:<redacted>@{tail}"
    return arg


def _resolve_token_safe() -> str | None:
    """Look up a GitHub token via the standard chain; swallow failures.

    The orchestrator expects clones to work without a token for public repos,
    so token-resolution failure is not fatal — we return None and let the
    clone proceed unauthenticated.
    """
    try:
        from minions.github.auth import get_github_token

        return get_github_token()
    except Exception as e:
        logger.info("no GitHub token available for working-tree clones: %s", e)
        return None
