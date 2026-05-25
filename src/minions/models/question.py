"""QuestionRecord — the inter-agent escalation channel.

When an agent can't answer something on its own (engineer crew hits an
ambiguous requirement, manager can't reconcile two proposals, …) it writes
a QuestionRecord targeting a higher-level role. If the target can't answer
either, the question escalates to the operator via the standard notifier.

Mirrors DecisionRecord in spirit — pydantic model, JSON+Postgres dual backend
through a factory, lightweight service layer for submit/answer/escalate.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class QuestionStatus(StrEnum):
    OPEN = "open"
    ANSWERED = "answered"
    ESCALATED = "escalated"  # bumped up to the operator
    CANCELLED = "cancelled"


class QuestionRecord(BaseModel):
    """A question raised by one agent for resolution by another role or the operator."""

    id: UUID = Field(default_factory=uuid4)
    project: str

    asker_role: str  # role that needs an answer (e.g., "engineer")
    asker_agent_id: str  # specific agent (e.g., "engineer@Demo")
    target_role: str  # role expected to answer (e.g., "manager", "operator")

    question: str  # the actual question, one sentence preferred
    context: str | None = None  # optional longer-form context (logs, links)

    # Optional link back to the work-unit that triggered the question.
    related_decision_id: UUID | None = None
    related_pr_url: str | None = None

    status: QuestionStatus = QuestionStatus.OPEN
    answer: str | None = None
    answered_by: str | None = None  # role or "operator"
    answered_at: datetime | None = None

    escalated_at: datetime | None = None
    escalation_reason: str | None = None

    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))
