"""Agent memory records loaded into crew prompts and exposed in the UI."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

AgentMemoryEvent = Literal[
    "task_started",
    "task_done",
    "pr_opened",
    "pr_merged",
    "review_received",
    "blocked",
    "lesson_learned",
    "sprint_planned",
]
AgentMemoryTier = Literal["hot", "cold"]


class AgentMemoryRecord(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    agent_id: str
    sprint_number: int | None = None
    decision_id: UUID | None = None
    task_id: UUID | None = None
    pr_url: str | None = None
    event: AgentMemoryEvent
    summary: str
    details: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    tier: AgentMemoryTier = "hot"
