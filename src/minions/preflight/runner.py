"""PreflightRunner — subprocess execution of project build/test commands.

Behavior (matches ``openspec/changes/engineer-preflight-execution/spec.md``):

* Apply the engineer's ``FilePatch`` list to a temp checkout (the caller
  passes an already-cloned working tree; we copy into a scratch dir).
* For each non-empty step (install → typecheck → build → test → lint),
  spawn a subprocess in ``cwd=scratch`` with:
  - secret-stripped env (``*_TOKEN`` / ``*_KEY`` / ``*_SECRET`` /
    ``*PASSWORD*`` removed; only an allowlist propagates),
  - per-step ``timeout`` from the config,
  - network sandbox per ``network:`` (best-effort).
* Required-step failure aborts; optional-step failure is reported but
  doesn't abort. ``test`` is a required step iff
  ``block_on_test_failure=True``.
* Scratch dir is cleaned up in a ``try/finally`` regardless of outcome.

Never executes anything from patched files directly — only the project's
own commands (npm/pnpm/uv/cargo/etc.) which resolve files via standard
tool lookup.
"""

from __future__ import annotations

import logging
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from minions.preflight.autodetect import effective_config
from minions.preflight.models import (
    ALL_STEPS,
    NetworkPosture,
    PreflightConfig,
    PreflightReport,
    PreflightStepResult,
)

logger = logging.getLogger(__name__)

# Env vars we KEEP. Anything else is dropped.
_ENV_ALLOWLIST: frozenset[str] = frozenset({
    "PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE",
    "NODE_ENV", "PYTHONUNBUFFERED", "USER", "SHELL",
    "TMPDIR", "TERM",
})
# Anything matching these patterns is stripped even if allowlisted.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r".*_TOKEN$"),
    re.compile(r".*_KEY$"),
    re.compile(r".*_SECRET$"),
    re.compile(r".*PASSWORD.*", re.IGNORECASE),
    re.compile(r"^AWS_.*"),
    re.compile(r"^GITHUB_.*"),
    re.compile(r"^ANTHROPIC_.*"),
)

MAX_TAIL_LINES = 200


# ---------------------------------------------------------------------------
# Public entrypoint.
# ---------------------------------------------------------------------------


def run_preflight(
    *,
    patches: Iterable[object],  # crews.engineer.FilePatch (avoid import cycle)
    manifest: object,           # models.manifest.Manifest
    repo_clone: Path,
    target_branch: str | None = None,
) -> PreflightReport:
    """Run preflight against ``patches`` applied on top of ``repo_clone``.

    ``patches`` is iterable of objects with ``.path`` and ``.content`` attrs
    (the engineer crew's ``FilePatch``). We type it loosely to avoid an
    import cycle with ``crews/engineer.py``.

    Returns a ``PreflightReport``. ``ok`` is True iff every required step
    passed. On any required-step failure, remaining steps are skipped.
    """
    config = effective_config(manifest.preflight, repo_clone)  # type: ignore[attr-defined]
    project_name = manifest.name  # type: ignore[attr-defined]

    if not config.enabled:
        return PreflightReport(
            project=project_name,
            commit_sha=_git_head(repo_clone) or "unknown",
            network_posture=config.network,
            ok=True,
            steps=[],
        )

    scratch = Path(tempfile.mkdtemp(prefix="minions-preflight-"))
    try:
        _populate_scratch(repo_clone, scratch, target_branch=target_branch)
        _apply_patches(patches, scratch)
        commit_sha = _git_head(scratch) or "unknown"

        started = datetime.now(UTC)
        steps: list[PreflightStepResult] = []
        ok = True
        for step_name in ALL_STEPS:
            command = getattr(config, step_name)
            if not command:
                continue
            result = _run_step(
                step=step_name,                   # type: ignore[arg-type]
                command=command,
                scratch=scratch,
                timeout=config.timeout_seconds,
                network=config.network,
            )
            steps.append(result)
            if not result.ok and _step_aborts(step_name, config):
                ok = False
                break

        return PreflightReport(
            project=project_name,
            commit_sha=commit_sha,
            started_at=started,
            finished_at=datetime.now(UTC),
            network_posture=config.network,
            steps=steps,
            ok=ok,
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


# ---------------------------------------------------------------------------
# Step execution.
# ---------------------------------------------------------------------------


def _step_aborts(step: str, config: PreflightConfig) -> bool:
    """A failed required step aborts; optional step failures don't.

    ``test`` is required iff ``block_on_test_failure``. ``lint`` is always
    optional.
    """
    if step in ("install", "typecheck", "build"):
        return True
    if step == "test":
        return config.block_on_test_failure
    return False


def _run_step(
    *,
    step: str,
    command: str,
    scratch: Path,
    timeout: int,
    network: NetworkPosture,
) -> PreflightStepResult:
    safe_env = _safe_env()
    wrapped = _apply_network_sandbox(command, posture=network, step=step)
    start = time.monotonic()
    timed_out = False
    try:
        proc = subprocess.run(
            wrapped,
            cwd=str(scratch),
            env=safe_env,
            shell=True,
            executable="/bin/bash",
            capture_output=True,
            timeout=timeout,
            text=True,
            check=False,
        )
        exit_code = proc.returncode
        stdout, stderr = proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as e:
        timed_out = True
        exit_code = 124
        stdout = (
            e.stdout.decode("utf-8", errors="replace")
            if isinstance(e.stdout, bytes)
            else (e.stdout or "")
        )
        stderr = (
            e.stderr.decode("utf-8", errors="replace") if isinstance(e.stderr, bytes)
            else (e.stderr or "")
        ) + f"\n[preflight] step '{step}' timed out after {timeout}s"
    duration = time.monotonic() - start
    ok = (exit_code == 0) and not timed_out
    return PreflightStepResult(
        step=step,                              # type: ignore[arg-type]
        command=command,
        exit_code=exit_code,
        stdout_tail=_tail(stdout),
        stderr_tail=_tail(stderr),
        duration_seconds=round(duration, 3),
        timed_out=timed_out,
        ok=ok,
    )


def _tail(text: str, max_lines: int = MAX_TAIL_LINES) -> str:
    if not text:
        return ""
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    return "[…truncated…]\n" + "\n".join(lines[-max_lines:])


# ---------------------------------------------------------------------------
# Network sandbox (best-effort).
# ---------------------------------------------------------------------------


def _apply_network_sandbox(
    command: str, *, posture: NetworkPosture, step: str
) -> str:
    """Wrap ``command`` per the network posture.

    ``deny``: try ``unshare -n`` on Linux; on macOS fall back to setting
    proxy env vars to a black-hole port. The fallback is best-effort —
    determined attackers can bypass it; the operator sees a WARN log so
    they know the posture.

    ``allow_install_only``: deny applies to every step EXCEPT ``install``.

    ``allow``: no wrapping.
    """
    if posture == NetworkPosture.ALLOW:
        return command
    if posture == NetworkPosture.ALLOW_INSTALL_ONLY and step == "install":
        return command
    if shutil.which("unshare"):
        # `unshare -n` drops network namespace. Available on Linux only.
        return f"unshare -n bash -lc {shlex.quote(command)}"
    logger.warning(
        "preflight: network sandbox falling back to proxy-blackhole (no `unshare` available)"
    )
    return (
        "HTTP_PROXY=http://127.0.0.1:1 "
        "HTTPS_PROXY=http://127.0.0.1:1 "
        "NO_PROXY= "
        f"{command}"
    )


# ---------------------------------------------------------------------------
# Env hygiene.
# ---------------------------------------------------------------------------


def _safe_env() -> dict[str, str]:
    out: dict[str, str] = {"CI": "1"}
    for key in _ENV_ALLOWLIST:
        if key in os.environ and not _is_secret(key):
            out[key] = os.environ[key]
    return out


def _is_secret(key: str) -> bool:
    return any(p.match(key) for p in _SECRET_PATTERNS)


# ---------------------------------------------------------------------------
# Scratch dir + patch application.
# ---------------------------------------------------------------------------


def _populate_scratch(
    repo_clone: Path, scratch: Path, *, target_branch: str | None = None
) -> None:
    """Copy ``repo_clone`` into ``scratch`` so we don't mutate the cache.

    Using ``cp -a`` (or ``shutil.copytree``) keeps the .git dir intact so
    later commands can introspect commit_sha + branch state.
    """
    # shutil.copytree requires the destination NOT to exist.
    shutil.rmtree(scratch, ignore_errors=True)
    shutil.copytree(repo_clone, scratch, symlinks=True, dirs_exist_ok=False)
    if target_branch:
        # Best-effort: checkout the target if it exists locally. Failure
        # is non-fatal; we still preflight against whatever HEAD is.
        with _SuppressSubproc():
            subprocess.run(
                ["git", "-C", str(scratch), "checkout", target_branch],
                check=False, capture_output=True, timeout=5,
            )


def _apply_patches(patches: Iterable[object], scratch: Path) -> None:
    for fp in patches:
        path = getattr(fp, "path", None)
        content = getattr(fp, "content", None)
        if not isinstance(path, str) or not isinstance(content, str):
            continue
        # Refuse path traversal.
        rel = Path(path)
        if rel.is_absolute() or any(p == ".." for p in rel.parts):
            logger.warning("preflight: refusing to write suspicious path %r", path)
            continue
        target = scratch / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)


def _git_head(root: Path) -> str | None:
    if not (root / ".git").exists():
        return None
    with _SuppressSubproc():
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=False, capture_output=True, text=True, timeout=5,
        )
        return proc.stdout.strip() or None
    return None


class _SuppressSubproc:
    """Tiny context manager that swallows subprocess + OS errors."""

    def __enter__(self) -> _SuppressSubproc:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:  # type: ignore[no-untyped-def]
        return exc_type is not None and issubclass(
            exc_type, (subprocess.SubprocessError, OSError)
        )


__all__ = ["run_preflight"]
