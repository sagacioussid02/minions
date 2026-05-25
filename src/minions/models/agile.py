"""Agile ritual and Product Manager spokesperson records."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

AgileRitual = Literal[
    "scrum",
    "sprint_planning",
    "monthly_planning",
    "monthly_demo",
]


class AgileRitualRecord(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    project: str
    ritual: AgileRitual
    period_start: datetime
    period_end: datetime
    summary: str
    blockers: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    related_decision_ids: list[str] = Field(default_factory=list)
    related_pr_urls: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PMAnswerRecord(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    project: str
    question: str
    answer: str
    citations: list[str] = Field(default_factory=list)
    escalated_to: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
