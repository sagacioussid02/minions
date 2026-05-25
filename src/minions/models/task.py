"""Task — one work chunk inside an approved Sprint Proposal.

Phase 3 of openspec/sprint-tasks-memory. A Task is what the refinement
crew produces from a single ``PlanItem``: a clickable, owner-assigned
unit of work that the engineer crew picks up.

  Decision (sprint proposal, approved)
    └── structured_plan: StructuredSprintPlan
          ├── features: [PlanItem, PlanItem]   ─┐
          ├── bugs:     [PlanItem]              │  refinement crew
          ├── ops:      [PlanItem]              ├──────────────────►  one Task per item
          └── docs:     [PlanItem]              │
                                                 ┘
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

TaskCategory = Literal["feature", "bug", "tech_debt", "ops", "docs"]
TaskStatus = Literal[
    "unassigned",  # no owner yet — every eligible candidate at WIP cap
    "queued",  # has an owner, waiting for engineer crew
    "in_progress",  # engineer crew working on it now
    "review",  # PR open, awaiting review/CI
    "done",  # PR merged
    "blocked",  # something went wrong, see merge_blocked_reason
    "cancelled",  # parent Decision was rejected or task superseded
]
EstimatedEffort = Literal["xs", "s", "m", "l", "xl"]


class Task(BaseModel):
    """One work chunk. Keyed by ``id`` but commonly looked up by decision_id."""

    model_config = ConfigDict(extra="allow")

    id: UUID = Field(default_factory=uuid4)
    decision_id: UUID  # parent sprint proposal
    project: str
    sprint_number: int | None = None  # copied from parent Decision

    category: TaskCategory
    title: str
    description: str
    acceptance_criteria: str = ""

    owner_role: str
    # Owner may be unset when the Task lands as ``status="unassigned"``
    # (every eligible candidate at WIP cap). Backlog sweep assigns later.
    owner_agent_id: str | None = None  # "engineer@Demo" — full agent id
    owner_display_name: str | None = None  # "Sasha"

    estimated_effort: EstimatedEffort = "m"
    status: TaskStatus = "queued"

    pr_url: str | None = None
    pr_number: int | None = None

    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))
    completed_at: datetime | None = None

    # Catch-all forward-compat slot (review notes, follow-up links, etc.).
    payload: dict[str, Any] = Field(default_factory=dict)
