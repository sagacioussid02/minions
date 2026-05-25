"""Minimal smoke for PreflightRunner — happy path, env hygiene, abort-on-fail.

Deliberately small (operator credit-constrained); broader matrix lands later.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from minions.preflight.models import NetworkPosture, PreflightConfig
from minions.preflight.runner import _safe_env, run_preflight


class _Patch:
    def __init__(self, path: str, content: str) -> None:
        self.path = path
        self.content = content


def _manifest(name: str = "p", overrides: dict[str, Any] | None = None) -> Any:
    """Mock manifest exposing only the attributes preflight reads."""
    o = overrides or {}
    return type("_M", (), {"name": name, "preflight": PreflightConfig(**o)})()


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    # An npm-shaped repo whose "build" step is just `true` so we don't need
    # a real toolchain. autodetect will pick install=npm ci which we OVERRIDE
    # via manifest to avoid hitting npm at all.
    (repo / "package.json").write_text(json.dumps({"scripts": {"build": "true"}}))
    (repo / "package-lock.json").write_text("{}")
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "init"], check=True)
    return repo


# ---------------------------------------------------------------------------
# Env hygiene — pure, no subprocess.
# ---------------------------------------------------------------------------


def test_safe_env_strips_secret_patterns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    monkeypatch.setenv("MY_TOKEN", "abc")
    monkeypatch.setenv("DB_PASSWORD", "x")
    monkeypatch.setenv("GITHUB_TOKEN", "gh")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("PATH", "/usr/bin")
    env = _safe_env()
    assert "PATH" in env
    assert env["CI"] == "1"
    for forbidden in ("ANTHROPIC_API_KEY", "MY_TOKEN", "DB_PASSWORD", "GITHUB_TOKEN", "AWS_REGION"):
        assert forbidden not in env, forbidden


# ---------------------------------------------------------------------------
# End-to-end: happy path + abort path.
# ---------------------------------------------------------------------------


def test_happy_path_runs_build_and_returns_ok(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    # Override commands so the test doesn't need npm/uv/etc.
    m = _manifest(
        overrides={
            "install": "true",
            "typecheck": "true",
            "build": "true",
            "network": NetworkPosture.ALLOW,
        }
    )
    report = run_preflight(patches=[], manifest=m, repo_clone=repo)
    assert report.ok
    assert len(report.steps) == 3
    assert {s.step for s in report.steps} == {"install", "typecheck", "build"}
    assert all(s.ok for s in report.steps)


def test_required_failure_aborts_and_marks_not_ok(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    m = _manifest(
        overrides={
            "install": "true",
            "build": "false",  # required step, exits 1 → abort
            "test": "true",  # should never run
            "network": NetworkPosture.ALLOW,
        }
    )
    report = run_preflight(patches=[], manifest=m, repo_clone=repo)
    assert not report.ok
    failed = report.failed_step
    assert failed is not None
    assert failed.step == "build"
    assert failed.exit_code != 0
    # test step was skipped because build aborted.
    assert all(s.step != "test" for s in report.steps)


def test_patches_land_in_scratch(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    # Build step reads the patched file; if the patch made it into scratch,
    # `grep` finds the marker string and exits 0.
    m = _manifest(
        overrides={
            "install": "true",
            "build": "grep -q SENTINEL marker.txt",
            "network": NetworkPosture.ALLOW,
        }
    )
    patches = [_Patch("marker.txt", "SENTINEL\n")]
    report = run_preflight(patches=patches, manifest=m, repo_clone=repo)
    assert report.ok, [s.stderr_tail for s in report.steps]


def test_disabled_preflight_is_noop(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    m = _manifest(overrides={"enabled": False})
    report = run_preflight(patches=[], manifest=m, repo_clone=repo)
    assert report.ok
    assert report.steps == []


def test_path_traversal_patch_is_skipped(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    m = _manifest(
        overrides={
            "install": "true",
            "build": "test ! -f ../escape.txt",  # passes only if traversal blocked
            "network": NetworkPosture.ALLOW,
        }
    )
    patches = [_Patch("../escape.txt", "should not be written")]
    report = run_preflight(patches=patches, manifest=m, repo_clone=repo)
    assert report.ok
