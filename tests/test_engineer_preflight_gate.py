"""Tests for the preflight gate wired into the engineer crew.

Patches ``resolve_working_tree`` + ``run_preflight`` so the test stays
fast + LLM-free; verifies the gate returns the right skipped EngineerResult
on preflight failure and skips silently when the working tree can't be
resolved.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from minions.crews.engineer import _run_preflight_gate
from minions.models.decision import Decision, DecisionType
from minions.models.manifest import load_manifest
from minions.preflight.models import (
    NetworkPosture,
    PreflightReport,
    PreflightStepResult,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _decision() -> Decision:
    return Decision(
        project="Demo",
        type=DecisionType.FEATURE,
        summary="x",
        rationale="y",
        proposer_role="manager",
        proposer_agent_id="manager@Demo",
    )


def _passing_report() -> PreflightReport:
    return PreflightReport(
        project="Demo", commit_sha="abc",
        network_posture=NetworkPosture.ALLOW, ok=True,
        steps=[PreflightStepResult(
            step="build", command="true", exit_code=0, ok=True,
        )],
    )


def _failing_report(step: str = "build") -> PreflightReport:
    return PreflightReport(
        project="Demo", commit_sha="abc",
        network_posture=NetworkPosture.ALLOW, ok=False,
        steps=[PreflightStepResult(
            step=step, command="false", exit_code=1,
            stderr_tail="Cannot find module 'foo'", ok=False,
        )],
    )


def test_gate_passes_when_preflight_ok(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "minions.working_tree.resolve_working_tree",
        lambda manifest, cache_dir=None: tmp_path,
        raising=False,
    )
    # The import lives inside the helper; patch where it's looked up.
    monkeypatch.setattr(
        "minions.preflight.runner.run_preflight",
        lambda **_kw: _passing_report(),
    )
    m = load_manifest(REPO_ROOT / "projects" / "Demo.yaml")
    files, state, skip = _run_preflight_gate(
        decision=_decision(), manifest=m, github=None,  # type: ignore[arg-type]
        api_key="k", eng_min=None,  # type: ignore[arg-type]
        allowed_files=[], task=None, retry_attempt=0,
    )
    assert skip is None
    assert state["ok"] is True
    assert state["attempted"] is True


def test_gate_skips_silently_when_working_tree_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(_m, cache_dir=None):
        raise RuntimeError("clone failed")

    monkeypatch.setattr(
        "minions.working_tree.resolve_working_tree", _raise, raising=False,
    )
    m = load_manifest(REPO_ROOT / "projects" / "Demo.yaml")
    files, state, skip = _run_preflight_gate(
        decision=_decision(), manifest=m, github=None,  # type: ignore[arg-type]
        api_key="k", eng_min=None,  # type: ignore[arg-type]
        allowed_files=[], task=None, retry_attempt=0,
    )
    assert skip is None  # silent skip — PR still opens
    assert state["attempted"] is False


def test_gate_returns_skip_when_no_api_key_for_retry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "minions.working_tree.resolve_working_tree",
        lambda manifest, cache_dir=None: tmp_path,
        raising=False,
    )
    monkeypatch.setattr(
        "minions.preflight.runner.run_preflight",
        lambda **_kw: _failing_report(),
    )
    m = load_manifest(REPO_ROOT / "projects" / "Demo.yaml")
    files, state, skip = _run_preflight_gate(
        decision=_decision(), manifest=m, github=None,  # type: ignore[arg-type]
        api_key=None, eng_min=None,  # type: ignore[arg-type]
        allowed_files=[], task=None, retry_attempt=0,
    )
    assert skip is not None
    assert "preflight failed" in skip
    assert state["step"] == "build"
    assert state["ok"] is False
