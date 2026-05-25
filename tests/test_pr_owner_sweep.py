"""Minimal smoke for the PR owner sweep — the pure helpers + dispatch path."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from minions.approval.store import DecisionStore
from minions.crews.engineer import EngineerResult
from minions.crews.engineer_runs_store import EngineerRunRecord, EngineerRunStore
from minions.models.decision import Decision, DecisionStatus, DecisionType
from minions.notify.base import Notifier
from minions.questions.store import QuestionStore
from minions.scheduled.pr_owner_sweep import (
    _classify_failure,
    _is_owner_actionable,
    run_pr_owner_sweep,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


class _SilentNotifier(Notifier):
    def notify_approval_request(self, decision: Decision) -> None: ...
    def notify_decision_resolved(self, decision: Decision) -> None: ...
    def notify_text(self, *, subject: str, body: str) -> None: ...


def _record(**kw: Any) -> EngineerRunRecord:
    defaults: dict[str, Any] = {
        "decision_id": _FIXED_DECISION_ID,
        "project": "Demo",
        "completed_at": datetime.now(tz=UTC),
        "pr_url": "https://x/p/1",
        "pr_number": 1,
        "branch_name": "minions/eng/x",
        "pr_state": "open",
        "owner_agent_id": "engineer@Demo#1",
    }
    defaults.update(kw)
    return EngineerRunRecord(**defaults)


_FIXED_DECISION_ID = "00000000-0000-4000-8000-000000000001"


def _decision() -> Decision:
    from uuid import UUID

    return Decision(
        id=UUID(_FIXED_DECISION_ID),
        project="Demo",
        type=DecisionType.FEATURE,
        summary="x",
        rationale="y",
        proposer_role="manager",
        proposer_agent_id="manager@Demo",
        status=DecisionStatus.EXECUTED,
    )


# --------------------------- pure helpers -----------------------------------


@pytest.mark.parametrize(
    ("ci", "merge", "expected"),
    [
        (None, "clean", None),
        ("success", "clean", None),
        ("failure", "clean", "ci_failure"),
        ("success", "dirty", "merge_conflict"),
        ("failure", "dirty", "merge_conflict"),  # conflict wins
    ],
)
def test_classify_failure(ci, merge, expected) -> None:
    assert _classify_failure(ci, merge) == expected


def test_is_owner_actionable_filters() -> None:
    assert _is_owner_actionable(_record())
    assert not _is_owner_actionable(_record(pr_state="merged"))
    assert not _is_owner_actionable(_record(pr_state="closed"))
    assert not _is_owner_actionable(_record(pr_number=None))
    assert not _is_owner_actionable(_record(branch_name=None))
    assert not _is_owner_actionable(_record(skipped=True))


# --------------------------- dispatch path ----------------------------------


class _FakeGH:
    def __init__(self, ci: str | None, merge: str | None) -> None:
        self.ci = ci
        self.merge = merge
        self.comments: list[str] = []

    def __enter__(self) -> _FakeGH:
        return self

    def __exit__(self, *_: Any) -> None:
        return None

    def get_pr_check_status(self, _n: int) -> tuple[str | None, str | None]:
        return self.ci, None

    def get_pr_merge_state(self, _n: int) -> str | None:
        return self.merge

    def comment_on_pull_request(self, *, number: int, body: str) -> None:
        self.comments.append(body)


def _open_gh(ci: str | None, merge: str | None):
    gh = _FakeGH(ci, merge)
    return lambda _m: gh, gh


def test_healthy_pr_is_left_alone(tmp_path: Path) -> None:
    runs = EngineerRunStore(tmp_path / "runs.json")
    decisions = DecisionStore(tmp_path / "dec.json")
    questions = QuestionStore(tmp_path / "q.json")
    rec = _record()
    runs.update(rec)
    decisions.save(_decision())

    open_gh, gh = _open_gh(ci="success", merge="clean")
    runner_called = {"n": 0}

    def _runner(*a: Any, **k: Any) -> EngineerResult:
        runner_called["n"] += 1
        raise AssertionError("runner must not run for healthy PRs")

    report = run_pr_owner_sweep(
        projects_dir=REPO_ROOT / "projects",
        store=decisions,
        engineer_runs_store=runs,
        questions_store=questions,
        open_github_client=open_gh,
        notifier=_SilentNotifier(),
        dry_run=False,
        runner=_runner,
    )
    assert runner_called["n"] == 0
    assert len(report.outcomes) == 1
    assert report.outcomes[0].status == "healthy"


def test_failing_pr_redispatches_owner_in_place(tmp_path: Path) -> None:
    runs = EngineerRunStore(tmp_path / "runs.json")
    decisions = DecisionStore(tmp_path / "dec.json")
    questions = QuestionStore(tmp_path / "q.json")
    rec = _record(followup_attempts=0)
    runs.update(rec)
    decisions.save(_decision())

    open_gh, gh = _open_gh(ci="failure", merge="clean")
    captured: dict[str, Any] = {}

    def _runner(decision: Decision, manifest: Any, **kw: Any) -> EngineerResult:
        captured["target_branch"] = kw.get("target_branch")
        captured["existing_pr_number"] = kw.get("existing_pr_number")
        captured["is_conflict_resolution"] = kw.get("is_conflict_resolution")
        captured["retry_attempt"] = kw.get("retry_attempt")
        return EngineerResult(
            decision_id=_FIXED_DECISION_ID,
            pr_url=rec.pr_url,
            pr_number=1,
            dry_run=False,
        )

    report = run_pr_owner_sweep(
        projects_dir=REPO_ROOT / "projects",
        store=decisions,
        engineer_runs_store=runs,
        questions_store=questions,
        open_github_client=open_gh,
        notifier=_SilentNotifier(),
        api_key="k",
        dry_run=False,
        runner=_runner,
    )
    assert captured["target_branch"] == "minions/eng/x"
    assert captured["existing_pr_number"] == 1
    assert captured["is_conflict_resolution"] is False
    assert captured["retry_attempt"] == 1
    # Counter is on the SAME record — sticky.
    updated = runs.get(_FIXED_DECISION_ID)
    assert updated is not None
    assert updated.followup_attempts == 1
    assert report.outcomes[0].status == "retried"
    # Zero new Decisions filed by the sweep.
    assert len(decisions.list_all()) == 1


def test_at_cap_escalates_and_skips_thereafter(tmp_path: Path) -> None:
    runs = EngineerRunStore(tmp_path / "runs.json")
    decisions = DecisionStore(tmp_path / "dec.json")
    questions = QuestionStore(tmp_path / "q.json")
    # Already at the default cap (3) — next sweep should escalate, not retry.
    rec = _record(followup_attempts=3)
    runs.update(rec)
    decisions.save(_decision())

    open_gh, gh = _open_gh(ci="failure", merge="clean")

    def _runner(*a: Any, **k: Any) -> EngineerResult:
        raise AssertionError("runner must not run when at cap")

    report = run_pr_owner_sweep(
        projects_dir=REPO_ROOT / "projects",
        store=decisions,
        engineer_runs_store=runs,
        questions_store=questions,
        open_github_client=open_gh,
        notifier=_SilentNotifier(),
        dry_run=False,
        runner=_runner,
    )
    assert report.outcomes[0].status == "escalated"
    assert report.outcomes[0].question_id is not None
    # The record now has escalated_question_id set; next sweep must skip.
    updated = runs.get(_FIXED_DECISION_ID)
    assert updated is not None
    assert updated.escalated_question_id is not None
    # ONE Question Record created.
    assert len(questions.list_all()) == 1

    # Re-run — must produce a skipped outcome (no new Q, no retry).
    report2 = run_pr_owner_sweep(
        projects_dir=REPO_ROOT / "projects",
        store=decisions,
        engineer_runs_store=runs,
        questions_store=questions,
        open_github_client=open_gh,
        notifier=_SilentNotifier(),
        dry_run=False,
        runner=_runner,
    )
    assert report2.outcomes[0].status == "skipped"
    assert "awaiting operator" in (report2.outcomes[0].reason or "")
    assert len(questions.list_all()) == 1  # idempotent
