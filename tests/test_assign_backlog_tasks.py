"""Backlog assignment sweep tests (Phase D of enriched-sprint-planning)."""

from __future__ import annotations

from pathlib import Path

from minions.models.task import Task
from minions.scheduled.assign_backlog_tasks import run_assign_backlog_tasks
from minions.tasks.store import TaskStore


def _unassigned_task(project: str, title: str, role: str = "engineer") -> Task:
    return Task(
        decision_id="00000000-0000-0000-0000-000000000001",  # type: ignore[arg-type]
        project=project,
        sprint_number=0,
        category="feature",
        title=title,
        description=f"Implement {title}",
        acceptance_criteria="Visible in app",
        owner_role=role,
        owner_agent_id=None,
        owner_display_name=None,
        estimated_effort="m",
        status="unassigned",
    )


def test_no_op_when_no_backlog(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "tasks.json")
    report = run_assign_backlog_tasks(task_store=store)
    assert report.assigned == 0
    assert report.kept_unassigned == 0


def test_assigns_unassigned_task_when_slot_open(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "tasks.json")
    task = _unassigned_task("Demo", "feat X")
    store.save(task)

    report = run_assign_backlog_tasks(task_store=store)

    assert report.assigned == 1
    assert report.kept_unassigned == 0
    saved = store.get(task.id)
    assert saved is not None
    assert saved.status == "queued"
    assert saved.owner_agent_id == "engineer@Demo"
    assert saved.owner_display_name == "Sasha"


def test_keeps_unassigned_when_all_candidates_at_cap(
    tmp_path: Path, monkeypatch
) -> None:
    """If every eligible owner is already at WIP cap, leave the Task alone."""
    import minions.crews.refinement as ref
    monkeypatch.setattr(ref, "MAX_WIP_PER_AGENT", 0)  # nothing fits

    store = TaskStore(tmp_path / "tasks.json")
    task = _unassigned_task("Demo", "feat Y")
    store.save(task)

    report = run_assign_backlog_tasks(task_store=store)

    assert report.assigned == 0
    assert report.kept_unassigned == 1
    saved = store.get(task.id)
    assert saved is not None
    assert saved.status == "unassigned"
    assert saved.owner_agent_id is None
