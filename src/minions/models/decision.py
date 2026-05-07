"""Decision Record — the unit of any material change in the org."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class DecisionType(StrEnum):
    FEATURE = "feature"
    BUG = "bug"
    DEP_UPGRADE = "dep_upgrade"
    INFRA = "infra"
    SECURITY = "security"
    COST = "cost"
    LICENSE = "license"
    TEAM_COMPOSITION = "team_composition"
    PROCUREMENT = "procurement"
    BUDGET_RAISE = "budget_raise"
    OTHER = "other"


class DecisionStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    TIMED_OUT = "timed_out"
    EXECUTED = "executed"


RiskScore = Literal["low", "medium", "high"]


class DevilsAdvocateCritique(BaseModel):
    """Pre-approval critique attached to any Decision Record at risk >= medium."""

    counter_argument: str
    failure_modes: list[str]
    alternative_considered: str | None = None


class Decision(BaseModel):
    """A material change proposal. Always recorded; gated by operator approval
    unless it matches a project-specific auto-approval rule.
    """

    model_config = ConfigDict(use_enum_values=False)

    id: UUID = Field(default_factory=uuid4)
    project: str
    type: DecisionType
    summary: str
    rationale: str
    diff_or_plan: str | None = None
    risk: RiskScore = "low"
    proposer_role: str
    proposer_agent_id: str
    # Human-readable name set at proposal time. Persisted on the record so renaming
    # an agent later doesn't rewrite history.
    proposer_display_name: str | None = None
    status: DecisionStatus = DecisionStatus.PENDING

    critique: DevilsAdvocateCritique | None = None

    # Cost-coupled approvals (e.g., team_composition + budget_raise) link via this field.
    paired_decision_id: UUID | None = None

    pr_url: str | None = None
    base_sha: str | None = None  # SHA on main this proposal was based on, for replay/audit

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    resolved_at: datetime | None = None
    resolved_reason: str | None = None
