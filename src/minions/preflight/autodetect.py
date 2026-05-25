"""Autodetect preflight commands from a repo's lockfile + manifest files.

Pure functions; no subprocess, no LLM. The caller supplies a path to a
working tree and gets back a ``PreflightConfig`` with sane defaults for
that toolchain. Explicit fields on the manifest's ``preflight:`` block
always win — autodetect only fills blanks.
"""

from __future__ import annotations

import json
from pathlib import Path

from minions.preflight.models import PreflightConfig


def autodetect(repo_root: Path) -> PreflightConfig:
    """Return a PreflightConfig populated from observable repo signals.

    Detection priority is lockfile-first: ``package-lock.json`` →
    ``pnpm-lock.yaml`` → ``yarn.lock`` → ``pyproject.toml`` →
    ``Cargo.toml`` → ``go.mod``. The first match wins.
    """
    if (repo_root / "package-lock.json").exists():
        return _from_npm(repo_root, install="npm ci")
    if (repo_root / "pnpm-lock.yaml").exists():
        return _from_npm(
            repo_root, install="pnpm install --frozen-lockfile",
            runner="pnpm",
        )
    if (repo_root / "yarn.lock").exists():
        return _from_npm(
            repo_root, install="yarn install --frozen-lockfile",
            runner="yarn",
        )
    if (repo_root / "pyproject.toml").exists():
        return _from_pyproject(repo_root)
    if (repo_root / "Cargo.toml").exists():
        return PreflightConfig(
            install="",                  # cargo resolves on build
            build="cargo build",
            test="cargo test",
            lint="cargo clippy --no-deps",
        )
    if (repo_root / "go.mod").exists():
        return PreflightConfig(
            install="go mod download",
            typecheck="go vet ./...",
            build="go build ./...",
            test="go test ./...",
        )
    return PreflightConfig()


def effective_config(
    manifest_config: PreflightConfig, repo_root: Path
) -> PreflightConfig:
    """Merge the operator's manifest block over autodetect.

    Non-empty fields on ``manifest_config`` win. Empty fields fall back
    to autodetect. ``enabled`` / ``network`` / ``timeout_seconds`` /
    ``block_on_test_failure`` always come from the manifest (autodetect
    never overrides policy knobs).
    """
    auto = autodetect(repo_root)
    return PreflightConfig(
        enabled=manifest_config.enabled,
        install=manifest_config.install or auto.install,
        typecheck=manifest_config.typecheck or auto.typecheck,
        build=manifest_config.build or auto.build,
        test=manifest_config.test or auto.test,
        lint=manifest_config.lint or auto.lint,
        timeout_seconds=manifest_config.timeout_seconds,
        network=manifest_config.network,
        block_on_test_failure=manifest_config.block_on_test_failure,
    )


# ---------------------------------------------------------------------------
# Per-toolchain helpers.
# ---------------------------------------------------------------------------


def _from_npm(
    repo_root: Path, *, install: str, runner: str = "npm"
) -> PreflightConfig:
    """Build a config from a Node lockfile + ``package.json``."""
    scripts = _read_package_scripts(repo_root)

    build = ""
    if "build" in scripts:
        build = f"{runner} run build" if runner == "npm" else f"{runner} build"

    typecheck = ""
    if "typecheck" in scripts:
        typecheck = f"{runner} run typecheck" if runner == "npm" else f"{runner} typecheck"
    elif (repo_root / "tsconfig.json").exists():
        typecheck = (
            f"{runner} exec tsc -- --noEmit"
            if runner == "npm"
            else f"{runner} exec tsc --noEmit"
        )

    test = ""
    if "test" in scripts:
        # `npm test` doesn't accept positional args; vitest-style runners
        # need `--run` to avoid watch mode. Operator can override.
        test = f"{runner} test" if runner != "npm" else "npm test"

    lint = ""
    if "lint" in scripts:
        lint = f"{runner} run lint" if runner == "npm" else f"{runner} lint"

    return PreflightConfig(
        install=install,
        typecheck=typecheck,
        build=build,
        test=test,
        lint=lint,
    )


def _from_pyproject(repo_root: Path) -> PreflightConfig:
    """uv-aware Python preflight defaults."""
    install = "uv sync --frozen"
    if (repo_root / "uv.lock").exists() is False and (
        repo_root / "poetry.lock"
    ).exists():
        install = "poetry install --no-root"
    typecheck = ""
    if any(_pyproject_has(repo_root, key) for key in ("[tool.mypy]", "mypy =")):
        typecheck = "mypy src"
    lint = ""
    if any(_pyproject_has(repo_root, key) for key in ("[tool.ruff]", "ruff =")):
        lint = "ruff check src tests"
    return PreflightConfig(
        install=install,
        typecheck=typecheck,
        build="",                # Python projects rarely "build" pre-test
        test="pytest -q",
        lint=lint,
    )


def _read_package_scripts(repo_root: Path) -> dict[str, str]:
    pkg = repo_root / "package.json"
    if not pkg.exists():
        return {}
    try:
        data = json.loads(pkg.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    scripts = data.get("scripts") or {}
    return scripts if isinstance(scripts, dict) else {}


def _pyproject_has(repo_root: Path, needle: str) -> bool:
    p = repo_root / "pyproject.toml"
    try:
        return needle in p.read_text()
    except OSError:
        return False


__all__ = ["autodetect", "effective_config"]
