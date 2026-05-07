"""Audit Findings — output of the independent Audit & Challenge team."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class FindingCategory(StrEnum):
    PROCESS = "process"
    CODE = "code"
    COST = "cost"
    SECURITY = "security"
    OTHER = "other"


class FindingStatus(StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


Severity = Literal["advisory", "medium", "high"]


class AuditFinding(BaseModel):
    """A finding produced by an audit agent. Severity routes notification urgency:
    high = immediate operator notify; medium = featured in Friday digest;
    advisory = aggregated.
    """

    id: UUID = Field(default_factory=uuid4)
    source_project: str | None = None
    source_decision_id: UUID | None = None
    source_pr_url: str | None = None

    category: FindingCategory
    severity: Severity
    summary: str
    evidence: str
    recommendation: str

    auditor_role: str
    auditor_agent_id: str

    status: FindingStatus = FindingStatus.OPEN

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    resolved_at: datetime | None = None
    resolution_notes: str | None = None
