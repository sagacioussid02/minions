"""Tests for the PR follow-up sweep."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from minions.approval.store import DecisionStore
from minions.crews.engineer import EngineerResult
from minions.crews.engineer_runs_store import EngineerRunRecord, EngineerRunStore
from minions.models.decision import DecisionStatus, DecisionType
from minions.notify.base import Notifier
from minions.scheduled.pr_followup import run_pr_followup

REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECTS_DIR = REPO_ROOT / "projects"


class _NullNotifier(Notifier):
    def __init__(self) -> None:
        self.approval_calls: list[Any] = []

    def notify_approval_request(self, decision: Any) -> None:
        self.approval_calls.append(decision)

    def notify_decision_resolved(self, decision: Any) -> None: ...


class _FakeGH:
    """In-memory GitHub stand-in. Records every comment posted + serves a
    pre-configured CI conclusion for a given PR number."""

    def __init__(self, ci_for: dict[int, tuple[str | None, str | None]]) -> None:
        self.ci_for = ci_for
        self.comments: list[tuple[int, str]] = []

    def __enter__(self) -> "_FakeGH":
        return self

    def __exit__(self, *_: Any) -> None:
        return None

    def get_pr_check_status(self, number: int) -> tuple[str | None, str | None]:
        return self.ci_for.get(number, (None, None))

    def comment_on_pull_request(self, *, number: int, body: str) -> None:
        self.comments.append((number, body))


def _seed_open_pr(project: str, pr_number: int, decision_id: str = "dec-1") -> EngineerRunRecord:
    return EngineerRunRecord(
        decision_id=decision_id,
        project=project,
        completed_at=datetime.now(tz=UTC),
        pr_url=f"https://github.com/x/{project}/pull/{pr_number}",
        pr_number=pr_number,
        branch_name=f"minions/eng/{project}-branch",
        files_changed=["README.md"],
        pr_state="open",
    )


def _save_record(store: EngineerRunStore, record: EngineerRunRecord) -> None:
    fake_result = EngineerResult(
        decision_id=record.decision_id,
        pr_url=record.pr_url,
        pr_number=record.pr_number,
        branch_name=record.branch_name,
        files_changed=record.files_changed,
        dry_run=False,
    )
    store.save(fake_result, project=record.project)


def test_success_ci_only_updates_record(tmp_path: Path) -> None:
    decisions = DecisionStore(tmp_path / "d.json")
    runs = EngineerRunStore(tmp_path / "r.json")
    _save_record(runs, _seed_open_pr("Demo", pr_number=42, decision_id="dec-ok"))

    gh = _FakeGH(ci_for={42: ("success", None)})

    report = run_pr_followup(
        projects_dir=PROJECTS_DIR,
        store=decisions,
        engineer_runs_store=runs,
        notifier=_NullNotifier(),
        open_github_client=lambda _m: gh,
    )

    assert report.queued_fixes == 0
    assert len(report.outcomes) == 1
    assert report.outcomes[0].status == "ok"
    assert report.outcomes[0].ci_conclusion == "success"

    # Record persisted with ci_conclusion populated.
    after = runs.get("dec-ok")
    assert after is not None
    assert after.ci_conclusion == "success"
    assert after.followup_attempts == 0
    # No fix decision was created.
    assert decisions.list_by_status(DecisionStatus.APPROVED) == []


def test_failure_queues_auto_approved_fix_decision(tmp_path: Path) -> None:
    decisions = DecisionStore(tmp_path / "d.json")
    runs = EngineerRunStore(tmp_path / "r.json")
    _save_record(runs, _seed_open_pr("Demo", pr_number=7, decision_id="dec-fail"))

    gh = _FakeGH(ci_for={7: ("failure", "https://ci/log/7")})
    notifier = _NullNotifier()

    report = run_pr_followup(
        projects_dir=PROJECTS_DIR,
        store=decisions,
        engineer_runs_store=runs,
        notifier=notifier,
        open_github_client=lambda _m: gh,
    )

    assert report.queued_fixes == 1

    # New fix Decision exists and is APPROVED (so execute-approved will pick it up).
    approved = decisions.list_by_status(DecisionStatus.APPROVED)
    assert len(approved) == 1
    fix = approved[0]
    assert fix.type is DecisionType.BUG
    assert fix.project == "Demo"
    assert fix.proposer_role == "pr_followup"
    assert "PR #7" in fix.summary or "pull/7" in (fix.diff_or_plan or "")

    # Comment posted on the failing PR.
    assert gh.comments and gh.comments[0][0] == 7
    assert "follow-up" in gh.comments[0][1].lower()

    # Counter bumped on original record.
    after = runs.get("dec-fail")
    assert after is not None
    assert after.followup_attempts == 1
    assert after.ci_conclusion == "failure"

    # The operator must NOT be paged — fix Decisions are internal traffic and
    # are auto-approved, so an approval-request email would be misleading noise.
    assert notifier.approval_calls == []


def test_failure_skipped_when_attempts_at_cap(tmp_path: Path) -> None:
    decisions = DecisionStore(tmp_path / "d.json")
    runs = EngineerRunStore(tmp_path / "r.json")
    rec = _seed_open_pr("Demo", pr_number=8, decision_id="dec-capped")
    rec.followup_attempts = 1  # already at default cap
    _save_record(runs, rec)
    # The save above overwrites our attempts field — patch it back via update.
    fresh = runs.get("dec-capped")
    assert fresh is not None
    fresh.followup_attempts = 1
    runs.update(fresh)

    gh = _FakeGH(ci_for={8: ("failure", None)})

    report = run_pr_followup(
        projects_dir=PROJECTS_DIR,
        store=decisions,
        engineer_runs_store=runs,
        notifier=_NullNotifier(),
        open_github_client=lambda _m: gh,
        max_attempts=1,
    )

    assert report.queued_fixes == 0
    assert report.outcomes[0].status == "skipped"
    assert gh.comments == []
    assert decisions.list_by_status(DecisionStatus.APPROVED) == []


def test_dry_run_does_not_persist_decision_or_comment(tmp_path: Path) -> None:
    decisions = DecisionStore(tmp_path / "d.json")
    runs = EngineerRunStore(tmp_path / "r.json")
    _save_record(runs, _seed_open_pr("Demo", pr_number=9, decision_id="dec-dry"))

    gh = _FakeGH(ci_for={9: ("failure", None)})

    report = run_pr_followup(
        projects_dir=PROJECTS_DIR,
        store=decisions,
        engineer_runs_store=runs,
        notifier=_NullNotifier(),
        open_github_client=lambda _m: gh,
        dry_run=True,
    )

    assert report.queued_fixes == 1  # outcome marked queued
    assert decisions.list_by_status(DecisionStatus.APPROVED) == []  # nothing persisted
    assert gh.comments == []  # no comments posted

    # And the counter is NOT bumped on dry-run (we didn't actually do work).
    after = runs.get("dec-dry")
    assert after is not None
    assert after.followup_attempts == 0


def test_skips_closed_or_merged_prs(tmp_path: Path) -> None:
    decisions = DecisionStore(tmp_path / "d.json")
    runs = EngineerRunStore(tmp_path / "r.json")

    rec = _seed_open_pr("Demo", pr_number=10, decision_id="dec-merged")
    rec.pr_state = "merged"
    _save_record(runs, rec)
    fresh = runs.get("dec-merged")
    assert fresh is not None
    fresh.pr_state = "merged"
    runs.update(fresh)

    gh = _FakeGH(ci_for={10: ("failure", None)})

    report = run_pr_followup(
        projects_dir=PROJECTS_DIR,
        store=decisions,
        engineer_runs_store=runs,
        notifier=_NullNotifier(),
        open_github_client=lambda _m: gh,
    )

    assert report.outcomes == []  # merged PR never reached
