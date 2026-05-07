"""Tests for the scheduled (cron) entrypoints."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from minions.approval.store import DecisionStore
from minions.models.decision import Decision, DecisionStatus, DecisionType
from minions.models.manifest import Manifest
from minions.scheduled import (
    run_daily_monitor,
    run_friday_digest,
    run_weekly_planning,
)


class _RecordingNotifier:
    def __init__(self) -> None:
        self.approval_requests: list[Decision] = []
        self.resolutions: list[Decision] = []
        self.texts: list[tuple[str, str]] = []

    def notify_approval_request(self, decision: Decision) -> None:
        self.approval_requests.append(decision)

    def notify_decision_resolved(self, decision: Decision) -> None:
        self.resolutions.append(decision)

    def notify_text(self, *, subject: str, body: str) -> None:
        self.texts.append((subject, body))


def _write_manifest(projects_dir: Path, name: str, source_path: Path) -> None:
    yaml_text = (
        f"name: {name}\n"
        f"description: test fixture\n"
        f"source:\n"
        f"  kind: local\n"
        f"  path: {source_path}\n"
        f"  default_branch: main\n"
        f"weekly_budget_usd: 1.0\n"
        f"monthly_budget_usd: 4.0\n"
        f"owner: t@t\n"
    )
    (projects_dir / f"{name}.yaml").write_text(yaml_text)


@pytest.fixture
def fake_portfolio(tmp_path: Path) -> tuple[Path, Path]:
    """Two tiny local projects in their own directories under tmp_path."""
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()

    proj_a = tmp_path / "alpha"
    proj_a.mkdir()
    (proj_a / "README.md").write_text("# alpha")
    (proj_a / "package.json").write_text(json.dumps({"dependencies": {"x": "1"}}))

    proj_b = tmp_path / "beta"
    proj_b.mkdir()
    (proj_b / "README.md").write_text("# beta")
    (proj_b / "openspec").mkdir()
    (proj_b / "openspec" / "tasks.md").write_text(
        "| 1 | a | ✅ Done | f |\n| 2 | b | ⬜ Todo | g |\n"
    )

    _write_manifest(projects_dir, "alpha", proj_a)
    _write_manifest(projects_dir, "beta", proj_b)

    return projects_dir, tmp_path


def test_daily_monitor_emits_one_entry_per_project(fake_portfolio: tuple[Path, Path]) -> None:
    projects_dir, _ = fake_portfolio
    report = run_daily_monitor(projects_dir=projects_dir)
    names = sorted(e.project for e in report.entries)
    assert names == ["alpha", "beta"]
    by_name = {e.project: e for e in report.entries}
    assert by_name["alpha"].status == "ok"
    assert by_name["beta"].status == "ok"
    assert by_name["beta"].tasks_remaining == 1


def test_daily_monitor_isolates_per_project_failure(
    fake_portfolio: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_dir, _ = fake_portfolio
    from minions.scheduled import daily_monitor as dm

    real = dm.build_profile

    def boom(manifest: Manifest, **kwargs: object) -> object:
        if manifest.name == "alpha":
            raise RuntimeError("forced failure")
        return real(manifest, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(dm, "build_profile", boom)
    report = run_daily_monitor(projects_dir=projects_dir)
    by_name = {e.project: e for e in report.entries}
    assert by_name["alpha"].status == "error"
    assert "forced failure" in (by_name["alpha"].error or "")
    assert by_name["beta"].status == "ok"


def test_weekly_planning_submits_one_decision_per_project(
    fake_portfolio: tuple[Path, Path], tmp_path: Path
) -> None:
    projects_dir, _ = fake_portfolio
    store = DecisionStore(tmp_path / "decisions.json")
    notifier = _RecordingNotifier()

    report = run_weekly_planning(
        projects_dir=projects_dir,
        store=store,
        notifier=notifier,
        dry_run=True,
    )
    assert report.submitted == 2
    assert report.errored == 0
    assert len(notifier.approval_requests) == 2
    assert len(store.list_all()) == 2


def test_weekly_planning_isolates_failure(
    fake_portfolio: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_dir, _ = fake_portfolio
    store = DecisionStore(tmp_path / "decisions.json")
    notifier = _RecordingNotifier()

    from minions.scheduled import weekly_planning as wp

    real = wp.run_planning_crew

    def maybe_boom(manifest: Manifest, **kwargs: object) -> object:
        if manifest.name == "alpha":
            raise RuntimeError("forced planning failure")
        return real(manifest, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(wp, "run_planning_crew", maybe_boom)

    report = run_weekly_planning(
        projects_dir=projects_dir,
        store=store,
        notifier=notifier,
        dry_run=True,
    )
    assert report.submitted == 1
    assert report.errored == 1
    by_proj = {o.project: o for o in report.outcomes}
    assert by_proj["alpha"].status == "error"
    assert by_proj["beta"].status == "submitted"


def test_friday_digest_aggregates_window_and_sends_via_notifier(
    fake_portfolio: tuple[Path, Path], tmp_path: Path
) -> None:
    projects_dir, _ = fake_portfolio
    store = DecisionStore(tmp_path / "decisions.json")
    notifier = _RecordingNotifier()

    # Seed two decisions: one pending, one approved.
    pending = Decision(
        project="alpha",
        type=DecisionType.FEATURE,
        summary="add thing",
        rationale="r",
        diff_or_plan="p",
        risk="low",
        proposer_role="manager",
        proposer_agent_id="mgr@alpha#1",
    )
    approved = Decision(
        project="beta",
        type=DecisionType.BUG,
        summary="fix thing",
        rationale="r",
        diff_or_plan="p",
        risk="low",
        proposer_role="manager",
        proposer_agent_id="mgr@beta#1",
        status=DecisionStatus.APPROVED,
    )
    store.save(pending)
    store.save(approved)

    report = run_friday_digest(
        projects_dir=projects_dir,
        store=store,
        notifier=notifier,
    )
    assert report.pending == 1
    assert report.approved == 1
    assert "Awaiting your review" in report.body
    assert len(notifier.texts) == 1
    assert notifier.texts[0][0] == "Minions weekly digest"


def test_friday_digest_does_not_send_when_send_false(
    fake_portfolio: tuple[Path, Path], tmp_path: Path
) -> None:
    projects_dir, _ = fake_portfolio
    store = DecisionStore(tmp_path / "decisions.json")
    notifier = _RecordingNotifier()
    run_friday_digest(
        projects_dir=projects_dir,
        store=store,
        notifier=notifier,
        send=False,
    )
    assert notifier.texts == []
