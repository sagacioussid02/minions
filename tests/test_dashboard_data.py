"""Tests for src/minions/dashboard/data.py — Streamlit-free."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from minions.approval.store import DecisionStore
from minions.config.portfolio import load_portfolio_config
from minions.cost import CostEntry, append_entry, set_log_path
from minions.dashboard.data import (
    ACTIVE_THRESHOLD_HOURS,
    IDLE_THRESHOLD_DAYS,
    AgentSummary,
    build_agent_summaries,
    build_dashboard_data,
    build_sprint_board,
)
from minions.models.decision import Decision, DecisionStatus, DecisionType
from minions.models.manifest import load_active_manifests

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def empty_log(tmp_path: Path) -> Path:
    p = tmp_path / "cost_log.jsonl"
    set_log_path(p)
    return p


def _seed_cost(
    *,
    project: str,
    role: str,
    when: datetime,
    cost_usd: float = 0.01,
    decision_id: str = "",
    log_path: Path | None = None,
) -> None:
    e = CostEntry(
        timestamp=when,
        project=project,
        decision_id=decision_id,
        role=role,
        model="claude-sonnet-4-6",
        input_tokens=100,
        output_tokens=50,
        cost_usd=cost_usd,
    )
    append_entry(e, path=log_path)


# ---- AgentSummary status ---------------------------------------------------


def test_agent_status_active_when_recent() -> None:
    a = AgentSummary(
        scope="project",
        project="p",
        role="manager",
        tier="sonnet",
        seats=1,
        seat_labels=["Marcus"],
        last_activity=datetime.now(tz=UTC) - timedelta(minutes=5),
        last_decision_id=None,
        calls_7d=1,
        cost_7d_usd=0.0,
        calls_total=1,
        cost_total_usd=0.0,
    )
    assert a.status == "active"


def test_agent_status_idle_between_24h_and_14d() -> None:
    a = AgentSummary(
        scope="project",
        project="p",
        role="manager",
        tier="sonnet",
        seats=1,
        seat_labels=["Marcus"],
        last_activity=datetime.now(tz=UTC) - timedelta(days=3),
        last_decision_id=None,
        calls_7d=0,
        cost_7d_usd=0.0,
        calls_total=1,
        cost_total_usd=0.0,
    )
    assert a.status == "idle"


def test_agent_status_stale_when_old() -> None:
    a = AgentSummary(
        scope="project",
        project="p",
        role="manager",
        tier="sonnet",
        seats=1,
        seat_labels=["Marcus"],
        last_activity=datetime.now(tz=UTC) - timedelta(days=30),
        last_decision_id=None,
        calls_7d=0,
        cost_7d_usd=0.0,
        calls_total=1,
        cost_total_usd=0.0,
    )
    assert a.status == "stale"


def test_agent_status_stale_when_never_invoked() -> None:
    a = AgentSummary(
        scope="project",
        project="p",
        role="manager",
        tier="sonnet",
        seats=1,
        seat_labels=["Marcus"],
        last_activity=None,
        last_decision_id=None,
        calls_7d=0,
        cost_7d_usd=0.0,
        calls_total=0,
        cost_total_usd=0.0,
    )
    assert a.status == "stale"


def test_thresholds_match_documented() -> None:
    assert ACTIVE_THRESHOLD_HOURS == 24.0
    assert IDLE_THRESHOLD_DAYS == 14


# ---- build_agent_summaries -------------------------------------------------


def test_summaries_include_every_role_in_template(empty_log: Path) -> None:
    """Roles defined in the per-project template appear even with zero activity."""
    manifests = load_active_manifests(REPO_ROOT / "projects")
    portfolio = load_portfolio_config(REPO_ROOT / "config" / "portfolio.yaml")
    summaries = build_agent_summaries(
        manifests=manifests, portfolio=portfolio, cost_log_path=empty_log
    )
    # Every project should have at least manager / product_owner / principal / engineer
    for project_name in manifests:
        roles_for_project = {s.role for s in summaries if s.project == project_name}
        assert {"manager", "product_owner", "principal_engineer", "engineer"} <= roles_for_project


def test_summaries_include_shared_layers(empty_log: Path) -> None:
    manifests = load_active_manifests(REPO_ROOT / "projects")
    portfolio = load_portfolio_config(REPO_ROOT / "config" / "portfolio.yaml")
    summaries = build_agent_summaries(
        manifests=manifests, portfolio=portfolio, cost_log_path=empty_log
    )
    shared_roles = {s.role for s in summaries if s.scope == "shared"}
    # Sample a few — exec + specialist + audit should all be represented.
    assert "ceo" in shared_roles or "cto" in shared_roles
    assert "chief_auditor" in shared_roles or "code_auditor" in shared_roles


def test_summaries_attribute_cost_to_correct_bucket(empty_log: Path) -> None:
    manifests = load_active_manifests(REPO_ROOT / "projects")
    portfolio = load_portfolio_config(REPO_ROOT / "config" / "portfolio.yaml")
    now = datetime.now(tz=UTC)
    _seed_cost(
        project="demo_five",
        role="manager",
        when=now - timedelta(hours=1),
        cost_usd=0.05,
        decision_id="dec-1",
        log_path=empty_log,
    )
    _seed_cost(
        project="demo_five",
        role="manager",
        when=now - timedelta(hours=2),
        cost_usd=0.03,
        decision_id="dec-1",
        log_path=empty_log,
    )
    _seed_cost(
        project="Demo",
        role="manager",
        when=now - timedelta(hours=1),
        cost_usd=0.10,
        log_path=empty_log,
    )

    summaries = build_agent_summaries(
        manifests=manifests, portfolio=portfolio, cost_log_path=empty_log, now=now
    )
    son_mgr = next(s for s in summaries if s.project == "demo_five" and s.role == "manager")
    Demo_mgr = next(s for s in summaries if s.project == "Demo" and s.role == "manager")
    other = next(s for s in summaries if s.project == "demo_three" and s.role == "manager")

    assert son_mgr.cost_total_usd == pytest.approx(0.08)
    assert son_mgr.calls_total == 2
    assert son_mgr.last_decision_id == "dec-1"
    assert son_mgr.status == "active"

    assert Demo_mgr.cost_total_usd == pytest.approx(0.10)

    assert other.last_activity is None
    assert other.status == "stale"
    assert other.calls_total == 0


def test_summaries_7d_window_filters(empty_log: Path) -> None:
    manifests = load_active_manifests(REPO_ROOT / "projects")
    portfolio = load_portfolio_config(REPO_ROOT / "config" / "portfolio.yaml")
    now = datetime.now(tz=UTC)
    _seed_cost(
        project="demo_five",
        role="manager",
        when=now - timedelta(days=10),
        cost_usd=1.00,
        log_path=empty_log,
    )
    _seed_cost(
        project="demo_five",
        role="manager",
        when=now - timedelta(days=2),
        cost_usd=0.05,
        log_path=empty_log,
    )

    summaries = build_agent_summaries(
        manifests=manifests, portfolio=portfolio, cost_log_path=empty_log, now=now
    )
    s = next(x for x in summaries if x.project == "demo_five" and x.role == "manager")
    assert s.calls_total == 2
    assert s.cost_total_usd == pytest.approx(1.05)
    assert s.calls_7d == 1
    assert s.cost_7d_usd == pytest.approx(0.05)


def test_summaries_multi_seat_role_lists_seats(empty_log: Path) -> None:
    """senior_engineer is multi-seat in the per-project template."""
    manifests = load_active_manifests(REPO_ROOT / "projects")
    portfolio = load_portfolio_config(REPO_ROOT / "config" / "portfolio.yaml")
    summaries = build_agent_summaries(
        manifests=manifests, portfolio=portfolio, cost_log_path=empty_log
    )
    sr = next(s for s in summaries if s.project == "demo_five" and s.role == "senior_engineer")
    assert sr.seats >= 1


# ---- build_sprint_board ----------------------------------------------------


def _decision(project: str, status: DecisionStatus, summary: str = "x") -> Decision:
    d = Decision(
        project=project,
        type=DecisionType.FEATURE,
        summary=summary,
        rationale="r",
        diff_or_plan="p",
        risk="low",
        proposer_role="manager",
        proposer_agent_id="m@p",
        status=status,
    )
    return d


def test_sprint_board_buckets_by_status() -> None:
    executed_with_pr = _decision("p", DecisionStatus.EXECUTED, "executed-1")
    object.__setattr__(executed_with_pr, "pr_url", "https://github.com/o/r/pull/1")

    decisions = [
        _decision("p", DecisionStatus.PENDING, "pending-1"),
        _decision("p", DecisionStatus.PENDING, "pending-2"),
        _decision("p", DecisionStatus.APPROVED, "approved-1"),
        executed_with_pr,
        _decision("p", DecisionStatus.REJECTED, "rejected-1"),
        _decision("other", DecisionStatus.PENDING, "ignored"),
    ]
    board = build_sprint_board(project="p", decisions=decisions)
    assert len(board.pending) == 2
    assert len(board.approved) == 1
    assert len(board.pr_open) == 1  # EXECUTED with pr_url
    assert len(board.done) == 1  # REJECTED
    assert board.total == 5
    assert all(
        d.project == "p"
        for col in [board.pending, board.approved, board.pr_open, board.done]
        for d in col
    )


def test_sprint_board_executed_without_pr_url_lands_in_progress() -> None:
    """An EXECUTED decision with no pr_url means engineer started but didn't open a PR."""
    decisions = [_decision("p", DecisionStatus.EXECUTED, "executed-no-pr")]
    board = build_sprint_board(project="p", decisions=decisions)
    assert len(board.pr_open) == 0
    assert len(board.in_progress) == 1


def test_sprint_board_merged_pr_lands_in_done(tmp_path: Path) -> None:
    """An EXECUTED decision whose run record is pr_state=merged moves to Done."""
    from minions.crews.engineer_runs_store import EngineerRunRecord

    d = _decision("p", DecisionStatus.EXECUTED, "shipped")
    object.__setattr__(d, "pr_url", "https://github.com/o/r/pull/1")
    rec = EngineerRunRecord(
        decision_id=str(d.id),
        project="p",
        completed_at=datetime.now(tz=UTC),
        pr_url="https://github.com/o/r/pull/1",
        pr_number=1,
        pr_state="merged",
        merged_at=datetime.now(tz=UTC),
    )
    board = build_sprint_board(project="p", decisions=[d], engineer_runs=[rec])
    assert len(board.pr_open) == 0
    assert len(board.done) == 1
    assert board.done[0].summary == "shipped"


def test_sprint_board_closed_pr_also_lands_in_done(tmp_path: Path) -> None:
    """Closed-without-merge is also terminal — sits in Done."""
    from minions.crews.engineer_runs_store import EngineerRunRecord

    d = _decision("p", DecisionStatus.EXECUTED, "abandoned")
    object.__setattr__(d, "pr_url", "https://github.com/o/r/pull/1")
    rec = EngineerRunRecord(
        decision_id=str(d.id),
        project="p",
        completed_at=datetime.now(tz=UTC),
        pr_url="https://github.com/o/r/pull/1",
        pr_number=1,
        pr_state="closed",
    )
    board = build_sprint_board(project="p", decisions=[d], engineer_runs=[rec])
    assert len(board.pr_open) == 0
    assert len(board.done) == 1


def test_sprint_board_in_progress_from_engineer_run_record(tmp_path: Path) -> None:
    """A run record without pr_url should put the decision in In Progress."""
    from minions.crews.engineer_runs_store import EngineerRunRecord

    decisions = [_decision("p", DecisionStatus.APPROVED, "approved-1")]
    decision_id = str(decisions[0].id)
    run = EngineerRunRecord(
        decision_id=decision_id,
        project="p",
        completed_at=datetime.now(tz=UTC),
        pr_url=None,  # engineer ran but didn't (yet) open a PR
    )
    board = build_sprint_board(project="p", decisions=decisions, engineer_runs=[run])
    assert len(board.approved) == 0
    assert len(board.in_progress) == 1


def test_sprint_board_in_progress_empty_in_phase_a() -> None:
    decisions = [_decision("p", DecisionStatus.APPROVED)]
    board = build_sprint_board(project="p", decisions=decisions)
    assert board.in_progress == []


def test_sprint_board_orders_newest_first() -> None:
    now = datetime.now(tz=UTC)
    older = _decision("p", DecisionStatus.PENDING, "older")
    object.__setattr__(older, "created_at", now - timedelta(hours=5))
    newer = _decision("p", DecisionStatus.PENDING, "newer")
    object.__setattr__(newer, "created_at", now - timedelta(hours=1))
    board = build_sprint_board(project="p", decisions=[older, newer])
    assert [d.summary for d in board.pending] == ["newer", "older"]


# ---- build_dashboard_data --------------------------------------------------


def test_build_dashboard_data_smoke(tmp_path: Path) -> None:
    log = tmp_path / "cost_log.jsonl"
    set_log_path(log)
    store_path = tmp_path / "decisions.json"
    DecisionStore(store_path)  # touches the file

    data = build_dashboard_data(
        projects_dir=REPO_ROOT / "projects",
        portfolio_config_path=REPO_ROOT / "config" / "portfolio.yaml",
        decision_store_path=store_path,
        cost_log_path=log,
    )
    assert data.agents
    assert data.pending_count == 0
    assert all(
        name in data.sprint_boards
        for name in ["demo_five", "Demo", "demo_three", "demo_four", "demo_two"]
    )


def test_daily_cost_series_returns_uniform_grid_per_project(empty_log: Path) -> None:
    from minions.dashboard.data import daily_cost_series

    now = datetime(2026, 5, 15, 12, 0, tzinfo=UTC)
    _seed_cost(
        project="a",
        role="manager",
        when=datetime(2026, 5, 14, 10, 0, tzinfo=UTC),
        cost_usd=0.10,
        log_path=empty_log,
    )
    _seed_cost(
        project="a",
        role="manager",
        when=datetime(2026, 5, 14, 11, 0, tzinfo=UTC),
        cost_usd=0.05,
        log_path=empty_log,
    )
    _seed_cost(
        project="b",
        role="manager",
        when=datetime(2026, 5, 13, 9, 0, tzinfo=UTC),
        cost_usd=0.20,
        log_path=empty_log,
    )

    series = daily_cost_series(days=7, cost_log_path=empty_log, now=now)
    assert set(series.keys()) == {"a", "b"}
    assert len(series["a"]) == 7  # uniform 7-day grid
    assert len(series["b"]) == 7
    a_total = sum(v for _, v in series["a"])
    b_total = sum(v for _, v in series["b"])
    assert a_total == pytest.approx(0.15)
    assert b_total == pytest.approx(0.20)


def test_daily_cost_series_empty_log_returns_empty_dict(empty_log: Path) -> None:
    from minions.dashboard.data import daily_cost_series

    assert daily_cost_series(cost_log_path=empty_log) == {}


def test_cost_series_for_filters_to_role(empty_log: Path) -> None:
    from minions.dashboard.data import cost_series_for

    now = datetime(2026, 5, 15, 12, 0, tzinfo=UTC)
    _seed_cost(
        project="a",
        role="manager",
        when=datetime(2026, 5, 14, 10, 0, tzinfo=UTC),
        cost_usd=0.10,
        log_path=empty_log,
    )
    _seed_cost(
        project="a",
        role="engineer",
        when=datetime(2026, 5, 14, 11, 0, tzinfo=UTC),
        cost_usd=99.0,
        log_path=empty_log,
    )

    series = cost_series_for("a", "manager", days=7, cost_log_path=empty_log, now=now)
    total = sum(v for _, v in series)
    assert total == pytest.approx(0.10)
    assert len(series) == 7


def test_cost_series_for_returns_zero_grid_when_no_data(empty_log: Path) -> None:
    from minions.dashboard.data import cost_series_for

    series = cost_series_for("a", "manager", days=7, cost_log_path=empty_log)
    assert len(series) == 7
    assert all(v == 0.0 for _, v in series)


def test_build_dashboard_data_pending_counter() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        store_path = Path(td) / "decisions.json"
        store = DecisionStore(store_path)
        store.save(_decision("demo_five", DecisionStatus.PENDING, "needs review"))
        store.save(_decision("demo_five", DecisionStatus.APPROVED))
        data = build_dashboard_data(
            projects_dir=REPO_ROOT / "projects",
            portfolio_config_path=REPO_ROOT / "config" / "portfolio.yaml",
            decision_store_path=store_path,
            cost_log_path=Path(td) / "cost_log.jsonl",
        )
        assert data.pending_count == 1
        assert data.approved_count == 1
