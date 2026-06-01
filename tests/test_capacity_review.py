"""Capacity watcher tests (openspec/changes/hire-as-decision)."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from minions.approval.store import DecisionStore
from minions.models.capacity import HireProposal
from minions.models.decision import Decision, DecisionStatus, DecisionType
from minions.models.task import Task
from minions.scheduled.capacity_review import (
    MAX_HIRES_PER_PORTFOLIO_PER_MONTH,
    run_capacity_review,
)
from minions.tasks.store import TaskStore


class _NullNotifier:
    def notify_approval_request(self, decision: Decision) -> None:  # noqa: D401
        pass

    def notify_decision_resolved(self, decision: Decision) -> None:
        pass


def _task(role: str, *, status: str, owner: str | None, project: str = "demo_three") -> Task:
    return Task(
        decision_id=uuid4(),
        project=project,
        sprint_number=0,
        category="tech_debt",
        title=f"{role} work {uuid4().hex[:5]}",
        description="x",
        owner_role=role,
        owner_agent_id=owner,
        estimated_effort="m",
        status=status,  # type: ignore[arg-type]
    )


def _stores(tmp_path: Path) -> tuple[TaskStore, DecisionStore]:
    return TaskStore(tmp_path / "tasks.json"), DecisionStore(tmp_path / "decisions.json")


def test_no_proposal_when_capacity_is_healthy(tmp_path: Path) -> None:
    tasks, decisions = _stores(tmp_path)
    tasks.save(_task("engineer", status="in_progress", owner="engineer@demo_three"))
    report = run_capacity_review(
        task_store=tasks, decision_store=decisions, notifier=_NullNotifier(), dry_run=True
    )
    assert report.proposed == 0
    assert all(o.status != "proposed" for o in report.outcomes)


def test_detects_overloaded_shared_seat(tmp_path: Path) -> None:
    tasks, decisions = _stores(tmp_path)
    # 6 open senior_engineer tasks on the single shared seat → over 1.5x cap (4.5).
    for _ in range(6):
        tasks.save(_task("senior_engineer", status="in_progress", owner="senior_engineer@shared"))
    report = run_capacity_review(
        task_store=tasks, decision_store=decisions, notifier=_NullNotifier(), dry_run=True
    )
    hit = [o for o in report.outcomes if o.role == "senior_engineer"]
    assert hit and hit[0].status == "dry_run"
    assert "open tasks" in hit[0].reason


def test_detects_unassigned_backlog(tmp_path: Path) -> None:
    tasks, decisions = _stores(tmp_path)
    for _ in range(5):
        tasks.save(_task("cloud_devops", status="unassigned", owner=None))
    report = run_capacity_review(
        task_store=tasks, decision_store=decisions, notifier=_NullNotifier(), dry_run=True
    )
    hit = [o for o in report.outcomes if o.role == "cloud_devops"]
    assert hit and "unassigned" in hit[0].reason


def test_files_decision_when_not_dry_run(tmp_path: Path) -> None:
    tasks, decisions = _stores(tmp_path)
    for _ in range(6):
        tasks.save(_task("senior_engineer", status="in_progress", owner="senior_engineer@shared"))
    report = run_capacity_review(
        task_store=tasks, decision_store=decisions, notifier=_NullNotifier(), dry_run=False
    )
    assert report.proposed >= 1
    filed = [d for d in decisions.list_all() if d.type == DecisionType.TEAM_COMPOSITION]
    assert len(filed) == report.proposed
    d = filed[0]
    assert d.status == DecisionStatus.PENDING
    # Structured block round-trips and validates.
    hp = HireProposal.model_validate(d.__pydantic_extra__["hire_proposal"])
    assert hp.role == "senior_engineer"
    assert hp.level == "senior"
    assert hp.justification


def test_rejection_cooldown_blocks_re_proposal(tmp_path: Path) -> None:
    tasks, decisions = _stores(tmp_path)
    for _ in range(6):
        tasks.save(_task("senior_engineer", status="in_progress", owner="senior_engineer@shared"))
    # A recently-rejected senior_engineer@shared hire.
    rejected = Decision(
        project="portfolio",
        type=DecisionType.TEAM_COMPOSITION,
        summary="Hire senior senior_engineer for shared",
        rationale="x",
        proposer_role="head_of_engineering",
        proposer_agent_id="head_of_engineering@shared",
        status=DecisionStatus.REJECTED,
    )
    rejected.hire_proposal = {"role": "senior_engineer", "scope": "shared"}  # type: ignore[attr-defined]
    decisions.save(rejected)

    report = run_capacity_review(
        task_store=tasks, decision_store=decisions, notifier=_NullNotifier(), dry_run=False
    )
    sr = [o for o in report.outcomes if o.role == "senior_engineer"]
    assert sr and sr[0].status == "skipped_cooldown"
    # No new decision filed for the cooled-down slot.
    assert len([d for d in decisions.list_all() if d.status == DecisionStatus.PENDING]) == 0


def test_portfolio_monthly_cap(tmp_path: Path) -> None:
    tasks, decisions = _stores(tmp_path)
    # Pre-fill the portfolio cap with pending hires this month.
    for i in range(MAX_HIRES_PER_PORTFOLIO_PER_MONTH):
        d = Decision(
            project="portfolio",
            type=DecisionType.TEAM_COMPOSITION,
            summary=f"prior hire {i}",
            rationale="x",
            proposer_role="head_of_engineering",
            proposer_agent_id="head_of_engineering@shared",
        )
        d.hire_proposal = {"role": "engineer", "scope": "demo_three"}  # type: ignore[attr-defined]
        decisions.save(d)
    for _ in range(6):
        tasks.save(_task("senior_engineer", status="in_progress", owner="senior_engineer@shared"))
    report = run_capacity_review(
        task_store=tasks, decision_store=decisions, notifier=_NullNotifier(), dry_run=False
    )
    assert report.proposed == 0
    assert any(o.status == "skipped_cap" for o in report.outcomes)
