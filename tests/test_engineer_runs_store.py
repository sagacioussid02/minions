"""Tests for src/minions/crews/engineer_runs_store.py."""

from __future__ import annotations

from pathlib import Path

from minions.crews.engineer import EngineerResult
from minions.crews.engineer_runs_store import (
    EngineerRunRecord,
    EngineerRunStore,
    PRReviewerAssignment,
)


def _result(**kwargs) -> EngineerResult:
    base = {
        "decision_id": "dec-1",
        "pr_url": "https://github.com/o/r/pull/1",
        "pr_number": 1,
        "branch_name": "minions/eng/x",
        "files_changed": ["a.py"],
        "files_rejected": [],
        "operator_comment_posted": True,
        "dry_run": False,
    }
    base.update(kwargs)
    return EngineerResult(**base)


def test_save_then_get_round_trip(tmp_path: Path) -> None:
    store = EngineerRunStore(tmp_path / "engineer_runs.json")
    record = store.save(_result(), project="demo_five")
    assert record.decision_id == "dec-1"
    assert record.project == "demo_five"
    assert record.pr_url == "https://github.com/o/r/pull/1"

    fetched = store.get("dec-1")
    assert fetched is not None
    assert fetched.pr_number == 1
    assert fetched.files_changed == ["a.py"]


def test_save_overwrites_same_decision_id(tmp_path: Path) -> None:
    """A re-run of the same decision should replace the prior record."""
    store = EngineerRunStore(tmp_path / "engineer_runs.json")
    store.save(_result(branch_name="branch-1"), project="p")
    store.save(_result(branch_name="branch-2"), project="p")
    rec = store.get("dec-1")
    assert rec is not None
    assert rec.branch_name == "branch-2"
    assert len(store.list_all()) == 1


def test_get_missing_returns_none(tmp_path: Path) -> None:
    store = EngineerRunStore(tmp_path / "engineer_runs.json")
    assert store.get("nope") is None


def test_list_by_project_filters(tmp_path: Path) -> None:
    store = EngineerRunStore(tmp_path / "engineer_runs.json")
    store.save(_result(decision_id="d1"), project="a")
    store.save(_result(decision_id="d2"), project="a")
    store.save(_result(decision_id="d3"), project="b")
    assert {r.decision_id for r in store.list_by_project("a")} == {"d1", "d2"}
    assert {r.decision_id for r in store.list_by_project("b")} == {"d3"}


def test_corrupt_file_returns_empty(tmp_path: Path) -> None:
    p = tmp_path / "engineer_runs.json"
    p.write_text("{not json")
    store = EngineerRunStore(p)
    assert store.list_all() == []
    # Subsequent save still works (overwrites the bad file).
    store.save(_result(), project="p")
    assert store.get("dec-1") is not None


def test_review_loop_state_round_trips(tmp_path: Path) -> None:
    store = EngineerRunStore(tmp_path / "engineer_runs.json")
    rec = store.save(_result(), project="p")
    rec.review_status = "assigned"
    rec.reviewers = [
        PRReviewerAssignment(
            role="ttl",
            agent_id="ttl@p",
            display_name="Tech Team Lead",
            status="approved",
            verdict="approve",
            summary="looks good",
        )
    ]
    store.update(rec)

    fetched = store.get("dec-1")
    assert fetched is not None
    assert fetched.review_status == "assigned"
    assert fetched.reviewers[0].role == "ttl"
    assert fetched.reviewers[0].verdict == "approve"


def test_iteration_count_back_compat_reads_legacy_followup_attempts() -> None:
    """JSON rows written before the rename still hydrate correctly."""
    legacy_payload = {
        "decision_id": "dec-legacy",
        "project": "p",
        "completed_at": "2026-05-01T00:00:00+00:00",
        "followup_attempts": 2,  # legacy key
    }
    record = EngineerRunRecord.model_validate(legacy_payload)
    assert record.iteration_count == 2


def test_iteration_count_prefers_new_key_when_both_present() -> None:
    """If both keys appear, the canonical iteration_count wins."""
    payload = {
        "decision_id": "dec-mixed",
        "project": "p",
        "completed_at": "2026-05-01T00:00:00+00:00",
        "iteration_count": 5,
        "followup_attempts": 99,  # legacy ignored
    }
    record = EngineerRunRecord.model_validate(payload)
    assert record.iteration_count == 5


def test_iteration_count_dump_emits_only_new_key() -> None:
    """Writes only the canonical key — no zombie `followup_attempts` on disk."""
    record = EngineerRunRecord(
        decision_id="dec-new",
        project="p",
        completed_at="2026-05-26T00:00:00+00:00",  # type: ignore[arg-type]
        iteration_count=4,
    )
    dumped = record.model_dump(mode="json")
    assert dumped["iteration_count"] == 4
    assert "followup_attempts" not in dumped
