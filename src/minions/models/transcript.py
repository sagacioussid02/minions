"""Crew transcript messages — per-task LLM output captured for the UI feed.

See ``openspec/changes/crew-transcripts/`` for the contract. One row per
agent task output across the multi-agent crews (planning's 5-voice debate,
discoverer's 4 role tasks, engineer + TTL review).

Run_id matches the activity_log's run_id, so transcripts join cleanly to
the Stage feed's ``agent_spoke`` events.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

RoleInConversation = Literal[
    "pitch",  # round-1 independent pitch (planning crew)
    "rebuttal",  # round-2 rebuttal (planning crew)
    "synthesis",  # manager/principal synthesis
    "review",  # TTL / peer review
    "task_output",  # plain per-task output (discoverer, engineer)
    "other",
]

MAX_MESSAGE_CHARS = 16_000
PREVIEW_CHARS = 240


class CrewTranscriptMessage(BaseModel):
    """One message in a crew's working session, attributed to a single agent."""

    id: UUID = Field(default_factory=uuid4)
    run_id: str
    project: str
    crew: str
    agent_role: str
    agent_display_name: str | None = None
    sequence: int
    role_in_conversation: RoleInConversation
    content: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
