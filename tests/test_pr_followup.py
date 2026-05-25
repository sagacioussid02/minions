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


def test_failure_does_not_queue_fix_decision(tmp_path: Path) -> None:
    """pr-ownership-sweep Phase 4: pr_followup MUST NOT file fix Decisions
    anymore. CI failure is handled by pr_owner_sweep dispatching the
    original owner in-place. pr_followup just records the CI snapshot."""
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

    # Zero new Decisions filed — that's the contract.
    assert decisions.list_by_status(DecisionStatus.APPROVED) == []
    assert decisions.list_by_status(DecisionStatus.PENDING) == []

    # CI snapshot still captured on the original record.
    after = runs.get("dec-fail")
    assert after is not None
    assert after.ci_conclusion == "failure"
    assert after.ci_last_checked_at is not None
    # Counter NOT touched by pr_followup — owner sweep owns it.
    assert after.followup_attempts == 0

    # Outcome shape: status=ok with reason explaining handoff.
    assert report.outcomes[0].status == "ok"
    assert "owner sweep" in (report.outcomes[0].reason or "")

    # No PR comment, no operator page — owner sweep posts its own
    # comment when it actually re-dispatches.
    assert gh.comments == []
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

    # pr-ownership-sweep Phase 4: pr_followup no longer files fix
    # Decisions at all (dry-run or otherwise). The dry-run path here
    # exercises the same "ci=failure → owner sweep will retry" exit.
    assert decisions.list_by_status(DecisionStatus.APPROVED) == []
    assert gh.comments == []

    # Counter not touched by pr_followup — owner sweep owns it.
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
