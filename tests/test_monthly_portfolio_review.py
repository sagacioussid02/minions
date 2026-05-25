"""Tests for the monthly portfolio review scheduled entrypoint."""

from __future__ import annotations

from pathlib import Path

import pytest

from minions.agile.store import AgileStore
from minions.approval.store import DecisionStore
from minions.crews.engineer_runs_store import EngineerRunStore
from minions.models.decision import Decision, DecisionStatus, DecisionType
from minions.scheduled import run_monthly_portfolio_review


@pytest.fixture(autouse=True)
def _disable_observability(monkeypatch: pytest.MonkeyPatch) -> None:
    from minions.crews import portfolio_review as crew_mod

    class _NoopRun:
        def __enter__(self) -> None:
            return None

        def __exit__(self, *args: object) -> bool:
            return False

    monkeypatch.setattr(crew_mod, "add_metadata", lambda **kwargs: None)
    monkeypatch.setattr(crew_mod, "crew_run", lambda **kwargs: _NoopRun())
    monkeypatch.setattr(crew_mod, "set_attribution", lambda **kwargs: None)
    monkeypatch.setattr(crew_mod, "clear_attribution", lambda: None)


class _RecordingNotifier:
    def __init__(self) -> None:
        self.approval_requests: list[Decision] = []
        self.resolutions: list[Decision] = []

    def notify_approval_request(self, decision: Decision) -> None:
        self.approval_requests.append(decision)

    def notify_decision_resolved(self, decision: Decision) -> None:
        self.resolutions.append(decision)


def _write_manifest(projects_dir: Path, name: str, source_path: Path) -> None:
    yaml_text = (
        f"name: {name}\n"
        "description: test fixture\n"
        "source:\n"
        "  kind: local\n"
        f"  path: {source_path}\n"
        "  default_branch: main\n"
        "weekly_budget_usd: 1.0\n"
        "monthly_budget_usd: 4.0\n"
        "owner: t@t\n"
    )
    (projects_dir / f"{name}.yaml").write_text(yaml_text)


@pytest.fixture
def stores(tmp_path: Path) -> tuple[DecisionStore, EngineerRunStore, _RecordingNotifier]:
    return (
        DecisionStore(tmp_path / "decisions.json"),
        EngineerRunStore(tmp_path / "runs.json"),
        _RecordingNotifier(),
    )


def test_monthly_review_submits_pending_decision(
    tmp_path: Path, stores: tuple[DecisionStore, EngineerRunStore, _RecordingNotifier]
) -> None:
    decisions, runs, notifier = stores
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    project_dir = tmp_path / "alpha"
    project_dir.mkdir()
    _write_manifest(projects_dir, "alpha", project_dir)

    report = run_monthly_portfolio_review(
        projects_dir=projects_dir,
        store=decisions,
        engineer_runs_store=runs,
        notifier=notifier,
        dry_run=True,
    )

    assert report.status == "submitted"
    assert report.submitted == 1
    assert report.projects_count == 1
    assert len(notifier.approval_requests) == 1
    saved = decisions.list_all()[0]
    assert saved.status is DecisionStatus.PENDING
    assert saved.type is DecisionType.PORTFOLIO_REVIEW


def test_monthly_review_handles_empty_inputs(
    tmp_path: Path, stores: tuple[DecisionStore, EngineerRunStore, _RecordingNotifier]
) -> None:
    decisions, runs, notifier = stores
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()

    report = run_monthly_portfolio_review(
        projects_dir=projects_dir,
        store=decisions,
        engineer_runs_store=runs,
        notifier=notifier,
        dry_run=True,
    )

    assert report.status == "submitted"
    assert report.projects_count == 0
    assert len(decisions.list_all()) == 1


def test_monthly_review_records_demo_artifact(
    tmp_path: Path, stores: tuple[DecisionStore, EngineerRunStore, _RecordingNotifier]
) -> None:
    decisions, runs, notifier = stores
    agile = AgileStore(tmp_path / "agile.json")
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    project_dir = tmp_path / "alpha"
    project_dir.mkdir()
    _write_manifest(projects_dir, "alpha", project_dir)

    report = run_monthly_portfolio_review(
        projects_dir=projects_dir,
        store=decisions,
        engineer_runs_store=runs,
        notifier=notifier,
        dry_run=True,
        agile_store=agile,
        activity_log_path=tmp_path / "activity.jsonl",
    )

    assert report.status == "submitted"
    demos = [r for r in agile.list_rituals("alpha") if r.ritual == "monthly_demo"]
    assert len(demos) == 1
    assert "monthly demo" in demos[0].summary


def test_monthly_review_attaches_devils_advocate(
    tmp_path: Path,
    stores: tuple[DecisionStore, EngineerRunStore, _RecordingNotifier],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decisions, runs, notifier = stores
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    calls: list[Decision] = []

    from minions.scheduled import monthly_portfolio_review as mod

    def fake_attach(decision: Decision, **kwargs: object) -> None:
        calls.append(decision)

    monkeypatch.setattr(mod, "attach_critique", fake_attach)
    monkeypatch.setattr(mod, "attach_security_review", lambda *args, **kwargs: None)

    report = run_monthly_portfolio_review(
        projects_dir=projects_dir,
        store=decisions,
        engineer_runs_store=runs,
        notifier=notifier,
        api_key="test-key",
        dry_run=True,
    )

    assert report.status == "submitted"
    assert len(calls) == 1
    assert calls[0].type is DecisionType.PORTFOLIO_REVIEW


def test_monthly_review_attaches_security_review(
    tmp_path: Path,
    stores: tuple[DecisionStore, EngineerRunStore, _RecordingNotifier],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decisions, runs, notifier = stores
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    calls: list[Decision] = []

    from minions.scheduled import monthly_portfolio_review as mod

    def fake_attach(decision: Decision, **kwargs: object) -> None:
        calls.append(decision)

    monkeypatch.setattr(mod, "attach_critique", lambda *args, **kwargs: None)
    monkeypatch.setattr(mod, "attach_security_review", fake_attach)

    report = run_monthly_portfolio_review(
        projects_dir=projects_dir,
        store=decisions,
        engineer_runs_store=runs,
        notifier=notifier,
        api_key="test-key",
        dry_run=True,
    )

    assert report.status == "submitted"
    assert len(calls) == 1
    assert calls[0].risk == "medium"
