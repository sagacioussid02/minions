"""Tests for the scheduled (cron) entrypoints."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from minions import activity
from minions.activity import read_log, set_log_path
from minions.agile.store import AgileStore
from minions.approval.store import DecisionStore
from minions.crews.engineer import EngineerResult
from minions.crews.engineer_runs_store import EngineerRunStore
from minions.models.decision import Decision, DecisionStatus, DecisionType
from minions.models.manifest import Manifest
from minions.models.question import QuestionRecord, QuestionStatus
from minions.questions.store import QuestionStore
from minions.scheduled import (
    run_crew_heartbeat,
    run_daily_monitor,
    run_friday_digest,
    run_scrum,
    run_weekly_planning,
)
from minions.transcripts.store import TranscriptStore


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


def test_crew_heartbeat_records_project_and_shared_checkins(
    fake_portfolio: tuple[Path, Path], tmp_path: Path
) -> None:
    projects_dir, _ = fake_portfolio
    activity_path = tmp_path / "activity.jsonl"
    set_log_path(activity_path)

    try:
        report = run_crew_heartbeat(projects_dir=projects_dir)

        assert report.errored == 0
        assert report.checked_in == 3
        by_scope = {outcome.scope: outcome for outcome in report.outcomes}
        assert "project:alpha" in by_scope
        assert "project:beta" in by_scope
        assert "shared" in by_scope
        assert "product_owner" in by_scope["project:alpha"].roles
        assert "engineer" in by_scope["project:alpha"].roles
        assert "ceo" in by_scope["shared"].roles
        assert "code_auditor" in by_scope["shared"].roles

        events = read_log(activity_path)
        assert [event.event for event in events] == [
            "crew_checkin",
            "crew_checkin",
            "crew_checkin",
        ]
        assert events[0].crew == "crew_heartbeat"
        assert "tech_team_lead" in events[0].agents
    finally:
        activity._log_path_override = None
        activity._force_jsonl = False


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
    decision = store.list_all()[0]
    assert decision.diff_or_plan is not None
    assert "## Planning conversation" in decision.diff_or_plan
    assert "Product Owner → Principal Engineer" in decision.diff_or_plan
    assert "Principal Engineer → Manager" in decision.diff_or_plan
    assert "Manager → Operator" in decision.diff_or_plan


def test_weekly_planning_records_sprint_ritual(
    fake_portfolio: tuple[Path, Path], tmp_path: Path
) -> None:
    projects_dir, _ = fake_portfolio
    store = DecisionStore(tmp_path / "decisions.json")
    agile = AgileStore(tmp_path / "agile.json")

    report = run_weekly_planning(
        projects_dir=projects_dir,
        store=store,
        notifier=_RecordingNotifier(),
        dry_run=True,
        projects=["alpha"],
        agile_store=agile,
        activity_log_path=tmp_path / "activity.jsonl",
    )

    assert report.submitted == 1
    rituals = agile.list_rituals("alpha")
    assert len(rituals) == 1
    assert rituals[0].ritual == "sprint_planning"
    assert rituals[0].related_decision_ids


def test_scrum_records_blockers_and_next_actions(
    fake_portfolio: tuple[Path, Path], tmp_path: Path
) -> None:
    projects_dir, _ = fake_portfolio
    decisions = DecisionStore(tmp_path / "decisions.json")
    runs = EngineerRunStore(tmp_path / "runs.json")
    agile = AgileStore(tmp_path / "agile.json")
    questions = QuestionStore(tmp_path / "questions.json")
    pending = Decision(
        project="alpha",
        type=DecisionType.FEATURE,
        summary="Needs approval",
        rationale="test",
        proposer_role="manager",
        proposer_agent_id="manager@alpha",
        status=DecisionStatus.PENDING,
    )
    decisions.save(pending)
    result = EngineerResult(
        decision_id=str(pending.id),
        pr_url="https://github.com/x/alpha/pull/1",
        pr_number=1,
        branch_name="minions/eng/test",
        files_changed=["README.md"],
    )
    run = runs.save(result, project="alpha")
    run.ci_conclusion = "failure"
    runs.update(run)
    q = QuestionRecord(
        project="alpha",
        asker_role="manager",
        asker_agent_id="manager@alpha",
        target_role="product_owner",
        question="What should ship next?",
        status=QuestionStatus.OPEN,
    )
    questions.save(q)

    report = run_scrum(
        projects_dir=projects_dir,
        store=decisions,
        engineer_runs_store=runs,
        agile_store=agile,
        questions_store=questions,
        activity_log_path=tmp_path / "activity.jsonl",
    )

    alpha = next(o for o in report.outcomes if o.project == "alpha")
    assert alpha.status == "recorded"
    assert any("failing CI" in b for b in alpha.blockers)
    assert any("awaiting operator approval" in b for b in alpha.blockers)
    ritual = agile.list_rituals("alpha")[0]
    assert ritual.ritual == "scrum"
    assert "alpha scrum" in ritual.summary


def test_scrum_emits_standup_round_table_transcript(
    fake_portfolio: tuple[Path, Path], tmp_path: Path
) -> None:
    """A scrum sweep should surface as a round-table meeting (crew_transcripts)."""
    projects_dir, _ = fake_portfolio
    decisions = DecisionStore(tmp_path / "decisions.json")
    runs = EngineerRunStore(tmp_path / "runs.json")
    agile = AgileStore(tmp_path / "agile.json")
    transcripts = TranscriptStore(tmp_path / "transcripts.json")
    decisions.save(
        Decision(
            project="alpha",
            type=DecisionType.FEATURE,
            summary="Needs approval",
            rationale="test",
            proposer_role="manager",
            proposer_agent_id="manager@alpha",
            status=DecisionStatus.PENDING,
        )
    )

    run_scrum(
        projects_dir=projects_dir,
        store=decisions,
        engineer_runs_store=runs,
        agile_store=agile,
        transcript_store=transcripts,
        activity_log_path=tmp_path / "activity.jsonl",
    )

    rows = [m for m in transcripts.list_all() if m.project == "alpha" and m.crew == "scrum"]
    assert len(rows) == 4
    assert {m.agent_role for m in rows} == {
        "manager",
        "product_owner",
        "tech_team_lead",
        "engineer",
    }
    # All four turns share one run_id (one meeting) and content is non-empty.
    assert len({m.run_id for m in rows}) == 1
    assert all(m.content.strip() for m in rows)
    # Lifecycle events emitted so the meeting resolves as completed.
    events = [json.loads(line) for line in (tmp_path / "activity.jsonl").read_text().splitlines()]
    scrum_events = {e["event"] for e in events if e.get("crew") == "scrum"}
    assert {"crew_started", "agent_spoke", "crew_finished"} <= scrum_events


def test_scrum_without_transcript_store_skips_round_table(
    fake_portfolio: tuple[Path, Path], tmp_path: Path
) -> None:
    """Backwards-compatible: no transcript_store → no transcript rows, no crash."""
    projects_dir, _ = fake_portfolio
    transcripts = TranscriptStore(tmp_path / "transcripts.json")
    report = run_scrum(
        projects_dir=projects_dir,
        store=DecisionStore(tmp_path / "decisions.json"),
        engineer_runs_store=EngineerRunStore(tmp_path / "runs.json"),
        agile_store=AgileStore(tmp_path / "agile.json"),
        activity_log_path=tmp_path / "activity.jsonl",
    )
    assert report.errored == 0
    assert transcripts.list_all() == []


def test_weekly_planning_can_scan_one_project(
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
        projects=["beta"],
    )

    assert report.submitted == 1
    assert [o.project for o in report.outcomes] == ["beta"]
    assert [d.project for d in store.list_all()] == ["beta"]


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
