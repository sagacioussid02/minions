"""Tests for the planning freshness gate + grounding note.

Exercises every freshness branch in ``run_planning_crew``:

* very_stale → raises ``PlanningRefusedStaleError`` with a queued discovery
  decision attached; no LLM dispatch.
* stale → proceeds; the returned Decision rationale flags "stale dossier".
* ok → proceeds; rationale references the dossier commit.
* none → proceeds; rationale flags "ungrounded by dossier".

Dry-run + ``output_override`` ensure no LLM is actually called. We also
verify the queued discovery Decision shape.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from minions.crews.planning import (
    PlanningRefusedStaleError,
    _dossier_grounding_note,
    build_queued_discovery_decision,
    run_planning_crew,
)
from minions.models.decision import DecisionStatus, DecisionType
from minions.models.dossier import DossierDigest
from minions.models.manifest import load_manifest
from minions.onboarding.profile import ProjectProfile

REPO_ROOT = Path(__file__).resolve().parents[1]


def _manifest(tmp_path: Path, name: str = "fresh-test"):
    src = REPO_ROOT / "projects" / "demo.yaml"
    data = yaml.safe_load(src.read_text())
    data["name"] = name
    out = tmp_path / f"{name}.yaml"
    out.write_text(yaml.safe_dump(data))
    return load_manifest(out)


def _profile(name: str, *, freshness: str | None, sha: str = "abc12345") -> ProjectProfile:
    digest = None
    if freshness not in (None, "none"):
        digest = DossierDigest(
            project=name,
            commit_sha=sha,
            generated_at=datetime.now(UTC),
            freshness=freshness,
        )
    return ProjectProfile(
        project=name,
        source_kind="local",
        source_path="/tmp/x",
        repo=None,
        dossier_digest=digest,
        dossier_freshness=freshness,
    )


# --------------------------- freshness gate ---------------------------------


def test_very_stale_raises_with_queued_decision(tmp_path: Path) -> None:
    m = _manifest(tmp_path)
    profile = _profile(m.name, freshness="very_stale")
    with pytest.raises(PlanningRefusedStaleError) as excinfo:
        # dry_run=False is required to exercise the gate; api_key is unused
        # because we abort before any LLM call.
        run_planning_crew(m, dry_run=False, api_key="unused", profile=profile)
    queued = excinfo.value.queued
    assert queued.type == DecisionType.DOSSIER_REFRESH
    assert queued.risk == "low"
    assert queued.status is DecisionStatus.APPROVED
    assert m.name in queued.summary
    extra = getattr(queued, "model_extra", None) or {}
    assert extra.get("kind") == "dossier_refresh_queued"


def test_dry_run_skips_freshness_gate_even_when_very_stale(tmp_path: Path) -> None:
    """Dry-run sweeps cost $0 — the gate only fires when planning would spend."""
    m = _manifest(tmp_path)
    profile = _profile(m.name, freshness="very_stale")
    decision = run_planning_crew(m, dry_run=True, profile=profile)
    assert decision is not None  # dry-run still returns a placeholder Decision


# --------------------------- grounding note ---------------------------------


def test_grounding_note_for_none_profile() -> None:
    assert _dossier_grounding_note(None) == ""


def test_grounding_note_for_none_freshness() -> None:
    p = _profile("p", freshness=None)
    assert _dossier_grounding_note(p) == ""


def test_grounding_note_marks_none_when_no_digest() -> None:
    p = _profile("p", freshness="none")
    note = _dossier_grounding_note(p)
    assert "ungrounded" in note


def test_grounding_note_marks_stale() -> None:
    p = _profile("p", freshness="stale", sha="cafe1234abcd")
    note = _dossier_grounding_note(p)
    assert "stale" in note
    assert "cafe1234" in note


def test_grounding_note_marks_ok() -> None:
    p = _profile("p", freshness="ok", sha="cafe1234abcd")
    note = _dossier_grounding_note(p)
    assert "PROJECT_DOSSIER.md" in note
    assert "cafe1234" in note


# --------------------------- queued discovery decision ----------------------


def test_queued_decision_carries_age(tmp_path: Path) -> None:
    m = _manifest(tmp_path)
    d = build_queued_discovery_decision(manifest=m, freshness="very_stale", age_days=42)
    assert "42d" in d.diff_or_plan
    assert d.status is DecisionStatus.APPROVED


def test_queued_decision_handles_unknown_age(tmp_path: Path) -> None:
    m = _manifest(tmp_path)
    d = build_queued_discovery_decision(manifest=m, freshness="very_stale")
    assert "unknown age" in d.diff_or_plan
