"""Refine approved Sprint Proposals into Tasks.

Phase 3 of openspec/sprint-tasks-memory. Sweeps every approved Decision
that has a ``structured_plan`` but no Tasks yet, runs the refinement
crew, persists Tasks. Cron every 15 min on the ``minions-cron``
concurrency group.

Idempotent: a Decision that already has Tasks is left alone. Safe to
re-run on the same set without producing duplicates.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from minions.approval.store_factory import DecisionStoreLike
from minions.crews.refinement import refine_decision
from minions.models.decision import DecisionStatus, DecisionType

if TYPE_CHECKING:
    from minions.tasks.store_factory import TaskStoreLike


class RefineOutcome(BaseModel):
    decision_id: str
    project: str
    status: Literal["refined", "skipped", "error"]
    task_count: int = 0
    reason: str | None = None


class RefineApprovedReport(BaseModel):
    started_at: str
    finished_at: str
    outcomes: list[RefineOutcome] = Field(default_factory=list)

    @property
    def refined(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "refined")

    @property
    def skipped(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "skipped")

    @property
    def errored(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "error")


def run_refine_approved(
    *,
    store: DecisionStoreLike,
    task_store: "TaskStoreLike",
) -> RefineApprovedReport:
    """Iterate approved sprint Decisions, emit Tasks for those without any."""
    started = datetime.now(tz=UTC).isoformat()
    approved = store.list_by_status(DecisionStatus.APPROVED)
    outcomes: list[RefineOutcome] = []

    for decision in approved:
        # Only sprint-proposal-shaped Decisions get refined.
        if decision.type != DecisionType.FEATURE:
            continue
        if decision.structured_plan is None:
            outcomes.append(RefineOutcome(
                decision_id=str(decision.id), project=decision.project,
                status="skipped", reason="no structured_plan",
            ))
            continue
        existing = task_store.list_by_decision(decision.id)
        if existing:
            outcomes.append(RefineOutcome(
                decision_id=str(decision.id), project=decision.project,
                status="skipped", reason=f"already refined ({len(existing)} tasks)",
            ))
            continue

        try:
            tasks = refine_decision(decision, task_store=task_store)
        except Exception as e:  # noqa: BLE001
            outcomes.append(RefineOutcome(
                decision_id=str(decision.id), project=decision.project,
                status="error", reason=str(e)[:120],
            ))
            continue
        outcomes.append(RefineOutcome(
            decision_id=str(decision.id), project=decision.project,
            status="refined", task_count=len(tasks),
        ))

    return RefineApprovedReport(
        started_at=started,
        finished_at=datetime.now(tz=UTC).isoformat(),
        outcomes=outcomes,
    )
