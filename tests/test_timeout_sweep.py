"""Tests for §3.5 — sweep_timeouts auto-reject."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from minions.approval.service import sweep_timeouts
from minions.approval.store import DecisionStore
from minions.models.decision import Decision, DecisionStatus, DecisionType


class _Recorder:
    def __init__(self) -> None:
        self.resolved: list[Decision] = []
        self.approval_requests: list[Decision] = []
        self.texts: list[tuple[str, str]] = []

    def notify_approval_request(self, decision: Decision) -> None:
        self.approval_requests.append(decision)

    def notify_decision_resolved(self, decision: Decision) -> None:
        self.resolved.append(decision)

    def notify_text(self, *, subject: str, body: str) -> None:
        self.texts.append((subject, body))


def _seed(store: DecisionStore, *, age_hours: float, status: DecisionStatus = DecisionStatus.PENDING) -> Decision:
    d = Decision(
        project="p",
        type=DecisionType.FEATURE,
        summary="x",
        rationale="r",
        diff_or_plan="plan",
        risk="low",
        proposer_role="manager",
        proposer_agent_id="mgr@p#1",
        status=status,
        created_at=datetime.now(tz=UTC) - timedelta(hours=age_hours),
    )
    store.save(d)
    return d


def test_sweep_rejects_only_old_pending(tmp_path: Path) -> None:
    store = DecisionStore(tmp_path / "decisions.json")
    notifier = _Recorder()
    fresh = _seed(store, age_hours=10)
    stale = _seed(store, age_hours=100)
    timed_out = sweep_timeouts(store=store, notifier=notifier, ttl_hours=72)

    assert [str(d.id) for d in timed_out] == [str(stale.id)]
    assert store.get(stale.id).status is DecisionStatus.REJECTED  # type: ignore[union-attr]
    assert store.get(fresh.id).status is DecisionStatus.PENDING   # type: ignore[union-attr]
    assert len(notifier.resolved) == 1


def test_sweep_uses_default_72h_when_omitted(tmp_path: Path) -> None:
    store = DecisionStore(tmp_path / "decisions.json")
    notifier = _Recorder()
    _seed(store, age_hours=71)  # under default
    stale = _seed(store, age_hours=73)
    timed_out = sweep_timeouts(store=store, notifier=notifier)
    assert [str(d.id) for d in timed_out] == [str(stale.id)]


def test_sweep_skips_already_resolved(tmp_path: Path) -> None:
    store = DecisionStore(tmp_path / "decisions.json")
    notifier = _Recorder()
    _seed(store, age_hours=200, status=DecisionStatus.APPROVED)
    _seed(store, age_hours=200, status=DecisionStatus.REJECTED)
    timed_out = sweep_timeouts(store=store, notifier=notifier, ttl_hours=72)
    assert timed_out == []


def test_sweep_records_reason(tmp_path: Path) -> None:
    store = DecisionStore(tmp_path / "decisions.json")
    notifier = _Recorder()
    stale = _seed(store, age_hours=100)
    sweep_timeouts(store=store, notifier=notifier, ttl_hours=72)
    after = store.get(stale.id)
    assert after is not None
    assert after.resolved_reason is not None
    assert "timeout" in after.resolved_reason.lower()
    assert "72" in after.resolved_reason


def test_sweep_returns_empty_on_empty_store(tmp_path: Path) -> None:
    store = DecisionStore(tmp_path / "decisions.json")
    assert sweep_timeouts(store=store, notifier=_Recorder()) == []


def test_sweep_ttl_can_be_zero_for_testing(tmp_path: Path) -> None:
    """Setting ttl=0 should immediately reject any pending decision."""
    store = DecisionStore(tmp_path / "decisions.json")
    notifier = _Recorder()
    fresh = _seed(store, age_hours=0.001)  # essentially "now"
    timed_out = sweep_timeouts(store=store, notifier=notifier, ttl_hours=0)
    assert len(timed_out) == 1


def test_daily_monitor_runs_sweep_when_store_provided(tmp_path: Path) -> None:
    """Wired into the daily cron entrypoint."""
    from minions.scheduled import run_daily_monitor

    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    proj = tmp_path / "alpha"
    proj.mkdir()
    (proj / "README.md").write_text("# alpha")
    (projects_dir / "alpha.yaml").write_text(
        "name: alpha\n"
        "description: x\n"
        f"source:\n  kind: local\n  path: {proj}\n  default_branch: main\n"
        "weekly_budget_usd: 1.0\nmonthly_budget_usd: 4.0\nowner: o@o\n"
    )

    store = DecisionStore(tmp_path / "decisions.json")
    notifier = _Recorder()
    stale = _seed(store, age_hours=100)

    report = run_daily_monitor(
        projects_dir=projects_dir,
        store=store,
        notifier=notifier,
        timeout_hours=72,
    )
    assert str(stale.id) in report.timed_out
    assert "Auto-rejected" in report.to_markdown()


def test_daily_monitor_skips_sweep_when_store_omitted(tmp_path: Path) -> None:
    from minions.scheduled import run_daily_monitor

    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    proj = tmp_path / "alpha"
    proj.mkdir()
    (proj / "README.md").write_text("# alpha")
    (projects_dir / "alpha.yaml").write_text(
        "name: alpha\n"
        "description: x\n"
        f"source:\n  kind: local\n  path: {proj}\n  default_branch: main\n"
        "weekly_budget_usd: 1.0\nmonthly_budget_usd: 4.0\nowner: o@o\n"
    )
    report = run_daily_monitor(projects_dir=projects_dir)
    assert report.timed_out == []
