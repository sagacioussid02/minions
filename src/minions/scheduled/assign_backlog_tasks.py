"""Backlog assignment sweep — re-runs the refinement load-balancer
against ``unassigned`` Tasks and flips them to ``queued`` when an
eligible agent's WIP drops below ``MAX_WIP_PER_AGENT``.

Phase D of openspec/enriched-sprint-planning. Cheap — when no Tasks are
in the unassigned state, this is a no-op SELECT + no writes.

Schedule: every 10 min (`.github/workflows/assign_backlog_tasks.yml`).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from minions.crews.refinement import _resolve_owner
from minions.models.sprint_plan import PlanItem

if TYPE_CHECKING:
    from minions.tasks.store_factory import TaskStoreLike


class AssignmentOutcome(BaseModel):
    task_id: str
    project: str
    status: Literal["assigned", "kept_unassigned"]
    new_owner_agent_id: str | None = None


class AssignBacklogReport(BaseModel):
    started_at: str
    finished_at: str
    outcomes: list[AssignmentOutcome] = Field(default_factory=list)

    @property
    def assigned(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "assigned")

    @property
    def kept_unassigned(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "kept_unassigned")


def run_assign_backlog_tasks(
    *,
    task_store: "TaskStoreLike",
) -> AssignBacklogReport:
    started = datetime.now(tz=UTC).isoformat()
    unassigned = [t for t in task_store.list_all() if t.status == "unassigned"]
    outcomes: list[AssignmentOutcome] = []
    if not unassigned:
        return AssignBacklogReport(
            started_at=started,
            finished_at=datetime.now(tz=UTC).isoformat(),
            outcomes=outcomes,
        )

    # Fresh load snapshot — counts queued/in_progress/review per agent.
    open_load = task_store.count_open_by_owner()

    for task in unassigned:
        # Reconstruct a minimal PlanItem so the resolver sees the same
        # signal it saw at refinement time. We don't have the original
        # suggested_owner_role on the Task, but we have owner_role (which
        # IS the resolved role). Pass it as suggested_owner_role so the
        # resolver picks the same candidate set.
        synthetic_item = PlanItem(
            title=task.title,
            suggested_owner_role=task.owner_role,
            estimated_effort=task.estimated_effort,
        )
        role, agent_id, display = _resolve_owner(
            item=synthetic_item,
            category=task.category,
            project=task.project,
            open_load=open_load,
        )
        if agent_id is None:
            outcomes.append(AssignmentOutcome(
                task_id=str(task.id), project=task.project,
                status="kept_unassigned",
            ))
            continue
        task.owner_agent_id = agent_id
        task.owner_display_name = display
        task.status = "queued"
        task_store.save(task)
        outcomes.append(AssignmentOutcome(
            task_id=str(task.id), project=task.project,
            status="assigned", new_owner_agent_id=agent_id,
        ))

    return AssignBacklogReport(
        started_at=started,
        finished_at=datetime.now(tz=UTC).isoformat(),
        outcomes=outcomes,
    )
