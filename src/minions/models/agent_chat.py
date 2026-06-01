"""AgentChat — operator-facing click-to-chat threads with a single agent.

Surface B of the living-org-spaces initiative. The operator picks a roster
seat (engineer@Demo#1 = "Vera"), opens a drawer, and talks to that agent
in first-person. The agent's persona is *simulated*: we render a system
prompt from the agent's role + dossier + active learning records + recent
transcripts and dispatch a single Anthropic call per turn.

Two records:

- ``AgentChatThread`` — one conversation between operator and a specific agent
- ``AgentChatMessage`` — one user or agent turn within a thread

Persisted via the standard 3-file dual-backend store pattern; see
``minions.agent_chat``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

ChatMessageRole = Literal["user", "agent"]


class AgentChatThread(BaseModel):
    """One operator <-> agent conversation."""

    id: UUID = Field(default_factory=uuid4)
    agent_id: str  # stable roster id, e.g. "engineer@Demo#1"
    project: str | None = None  # None for shared/exec agents (agent_id ends with '@org')
    title: str | None = None  # short summary, auto-derived from first user message
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))
    last_message_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))


class AgentChatMessage(BaseModel):
    """One turn in a thread. Operator messages have role='user'; replies role='agent'."""

    id: UUID = Field(default_factory=uuid4)
    thread_id: UUID
    role: ChatMessageRole
    content: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))

    # Populated for role='agent' turns only.
    model: str | None = None
    prompt_tokens: int | None = None
    response_tokens: int | None = None
