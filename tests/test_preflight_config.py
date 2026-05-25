"""Tests for preflight models + manifest wiring + autodetect."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from minions.models.manifest import load_manifest
from minions.preflight.autodetect import autodetect, effective_config
from minions.preflight.models import (
    ALL_STEPS,
    REQUIRED_STEPS,
    NetworkPosture,
    PreflightConfig,
    PreflightReport,
    PreflightStepResult,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


# --------------------------- models -----------------------------------------


def test_preflight_config_defaults() -> None:
    c = PreflightConfig()
    assert c.enabled is True
    assert c.install == ""
    assert c.build == ""
    assert c.network is NetworkPosture.ALLOW_INSTALL_ONLY
    assert c.block_on_test_failure is True
    assert c.timeout_seconds == 600


def test_preflight_config_rejects_zero_timeout() -> None:
    with pytest.raises(ValidationError):
        PreflightConfig(timeout_seconds=0)


def test_required_steps_constants() -> None:
    assert REQUIRED_STEPS == ("install", "typecheck", "build")
    assert "test" in ALL_STEPS
    assert "lint" in ALL_STEPS


def test_report_failed_step_returns_first_failure() -> None:
    r = PreflightReport(
        project="p", commit_sha="abc",
        network_posture=NetworkPosture.DENY,
        ok=False,
        steps=[
            PreflightStepResult(step="install", command="npm ci", exit_code=0, ok=True),
            PreflightStepResult(step="build", command="npm run build",
                                 exit_code=1, stderr_tail="boom", ok=False),
        ],
    )
    failed = r.failed_step
    assert failed is not None
    assert failed.step == "build"
    assert "boom" in failed.stderr_tail


# --------------------------- manifest wiring --------------------------------


def test_manifest_default_preflight_present() -> None:
    m = load_manifest(REPO_ROOT / "projects" / "Demo.yaml")
    assert isinstance(m.preflight, PreflightConfig)
    assert m.preflight.enabled is True
    assert m.preflight.install == ""   # autodetect-driven


def test_manifest_preflight_override(tmp_path: Path) -> None:
    src = REPO_ROOT / "projects" / "Demo.yaml"
    data = yaml.safe_load(src.read_text())
    data["preflight"] = {
        "enabled": True,
        "install": "npm ci",
        "build": "npm run build",
        "network": "deny",
        "timeout_seconds": 120,
    }
    out = tmp_path / "Demo.yaml"
    out.write_text(yaml.safe_dump(data))
    m = load_manifest(out)
    assert m.preflight.install == "npm ci"
    assert m.preflight.build == "npm run build"
    assert m.preflight.network is NetworkPosture.DENY
    assert m.preflight.timeout_seconds == 120


def test_manifest_preflight_disabled(tmp_path: Path) -> None:
    src = REPO_ROOT / "projects" / "Demo.yaml"
    data = yaml.safe_load(src.read_text())
    data["preflight"] = {"enabled": False}
    out = tmp_path / "Demo.yaml"
    out.write_text(yaml.safe_dump(data))
    m = load_manifest(out)
    assert m.preflight.enabled is False


# --------------------------- autodetect -------------------------------------


def _write_npm_repo(root: Path, *, scripts: dict[str, str]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "package.json").write_text(json.dumps({"scripts": scripts}))
    (root / "package-lock.json").write_text("{}")
    return root


def test_autodetect_npm_with_build_script(tmp_path: Path) -> None:
    repo = _write_npm_repo(
        tmp_path / "npm", scripts={"build": "next build", "test": "vitest"}
    )
    c = autodetect(repo)
    assert c.install == "npm ci"
    assert c.build == "npm run build"
    assert c.test == "npm test"
    assert c.typecheck == ""  # no tsconfig.json


def test_autodetect_npm_with_tsconfig_adds_typecheck(tmp_path: Path) -> None:
    repo = _write_npm_repo(tmp_path / "npm-ts", scripts={"build": "next build"})
    (repo / "tsconfig.json").write_text("{}")
    c = autodetect(repo)
    assert "tsc" in c.typecheck


def test_autodetect_pnpm(tmp_path: Path) -> None:
    repo = tmp_path / "pnpm"
    repo.mkdir()
    (repo / "package.json").write_text(json.dumps({"scripts": {"build": "x"}}))
    (repo / "pnpm-lock.yaml").write_text("lockfile: ...")
    c = autodetect(repo)
    assert c.install.startswith("pnpm install")
    assert c.build == "pnpm build"


def test_autodetect_yarn(tmp_path: Path) -> None:
    repo = tmp_path / "yarn"
    repo.mkdir()
    (repo / "package.json").write_text(json.dumps({"scripts": {"build": "x"}}))
    (repo / "yarn.lock").write_text("")
    c = autodetect(repo)
    assert c.install.startswith("yarn install")
    assert c.build == "yarn build"


def test_autodetect_python_uv(tmp_path: Path) -> None:
    repo = tmp_path / "py"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("[tool.mypy]\n[tool.ruff]\n")
    (repo / "uv.lock").write_text("")
    c = autodetect(repo)
    assert c.install == "uv sync --frozen"
    assert c.typecheck == "mypy src"
    assert c.test == "pytest -q"
    assert c.lint == "ruff check src tests"
    assert c.build == ""  # Python rarely "builds"


def test_autodetect_python_poetry(tmp_path: Path) -> None:
    repo = tmp_path / "py"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("")
    (repo / "poetry.lock").write_text("")
    c = autodetect(repo)
    assert c.install == "poetry install --no-root"
    assert c.test == "pytest -q"


def test_autodetect_cargo(tmp_path: Path) -> None:
    repo = tmp_path / "rust"
    repo.mkdir()
    (repo / "Cargo.toml").write_text("")
    c = autodetect(repo)
    assert c.install == ""
    assert c.build == "cargo build"
    assert c.test == "cargo test"


def test_autodetect_go(tmp_path: Path) -> None:
    repo = tmp_path / "go"
    repo.mkdir()
    (repo / "go.mod").write_text("")
    c = autodetect(repo)
    assert c.install == "go mod download"
    assert c.typecheck == "go vet ./..."
    assert c.build == "go build ./..."


def test_autodetect_unknown_returns_blank(tmp_path: Path) -> None:
    repo = tmp_path / "empty"
    repo.mkdir()
    c = autodetect(repo)
    assert c.install == ""
    assert c.build == ""
    assert c.test == ""


def test_autodetect_smokes_minions_repo() -> None:
    """Self-check: minions repo autodetects as uv + mypy + pytest + ruff."""
    c = autodetect(REPO_ROOT)
    assert c.install == "uv sync --frozen"
    assert c.typecheck == "mypy src"
    assert c.test == "pytest -q"
    assert c.lint == "ruff check src tests"


# --------------------------- effective_config -------------------------------


def test_effective_config_manifest_wins_over_autodetect(tmp_path: Path) -> None:
    repo = _write_npm_repo(tmp_path / "npm", scripts={"build": "next build"})
    manifest = PreflightConfig(build="custom-build", network=NetworkPosture.DENY)
    eff = effective_config(manifest, repo)
    assert eff.build == "custom-build"  # manifest override
    assert eff.install == "npm ci"      # autodetect fills blank
    assert eff.network is NetworkPosture.DENY  # policy always from manifest


def test_effective_config_preserves_policy_knobs(tmp_path: Path) -> None:
    repo = _write_npm_repo(tmp_path / "npm", scripts={})
    manifest = PreflightConfig(
        enabled=False, timeout_seconds=10, block_on_test_failure=False
    )
    eff = effective_config(manifest, repo)
    assert eff.enabled is False
    assert eff.timeout_seconds == 10
    assert eff.block_on_test_failure is False
