"""Spokesperson interview records."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

InterviewScope = Literal["project", "organization"]
InterviewRole = Literal["operator", "spokesperson", "consulted_agent"]
Confidence = Literal["high", "medium", "low", "unknown"]
ConsultationStatus = Literal[
    "queued",
    "gathering_memory",
    "scanning_code",
    "answered",
    "blocked",
]
CitationSource = Literal[
    "manifest",
    "readme",
    "docs",
    "decision",
    "pull_request",
    "agile_ritual",
    "activity",
    "cost",
    "role_memory",
    "code_scan",
    "consultation",
]


class InterviewCitation(BaseModel):
    source_type: CitationSource
    label: str
    reference: str | None = None
    excerpt: str


class InterviewThreadRecord(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    scope: InterviewScope
    project: str | None = None
    spokesperson_role: str
    title: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ConsultationRecord(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    thread_id: UUID
    message_id: UUID
    project: str | None = None
    consulted_role: str
    status: ConsultationStatus = "queued"
    memory_summary: str | None = None
    code_scan_summary: str | None = None
    files_inspected: list[str] = Field(default_factory=list)
    note: str | None = None
    citations: list[InterviewCitation] = Field(default_factory=list)
    confidence: Confidence = "unknown"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class InterviewTaskProposal(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    thread_id: UUID
    message_id: UUID
    project: str | None = None
    owner_role: str
    title: str
    rationale: str
    status: Literal["pending", "converted", "dismissed"] = "pending"
    decision_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class InterviewMessageRecord(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    thread_id: UUID
    role: InterviewRole
    agent_role: str | None = None
    content: str
    citations: list[InterviewCitation] = Field(default_factory=list)
    consulted_roles: list[str] = Field(default_factory=list)
    confidence: Confidence = "unknown"
    follow_up_actions: list[str] = Field(default_factory=list)
    task_proposal_id: UUID | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
