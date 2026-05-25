"""Durable agent learning records."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

LearningKind = Literal[
    "technical",
    "product",
    "ops",
    "process",
    "risk",
    "preference",
]
LearningConfidence = Literal["high", "medium", "low"]


class AgentLearningRecord(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    agent_id: str
    role: str
    project: str | None = None
    kind: LearningKind
    fact: str
    source_type: str
    source_id: str
    confidence: LearningConfidence
    embedding_key: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_used_at: datetime | None = None
    superseded_by: UUID | None = None
    expires_at: datetime | None = None
