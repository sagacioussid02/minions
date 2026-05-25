"""InterviewStore backend selector."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol
from uuid import UUID

from minions.db.connection import has_database_url
from minions.models.interview import (
    ConsultationRecord,
    InterviewMessageRecord,
    InterviewTaskProposal,
    InterviewThreadRecord,
)


class InterviewStoreLike(Protocol):
    def save_thread(self, record: InterviewThreadRecord) -> InterviewThreadRecord: ...
    def get_thread(self, thread_id: UUID | str) -> InterviewThreadRecord | None: ...
    def list_threads(self, project: str | None = None) -> list[InterviewThreadRecord]: ...
    def save_message(self, record: InterviewMessageRecord) -> InterviewMessageRecord: ...
    def list_messages(self, thread_id: UUID | str) -> list[InterviewMessageRecord]: ...
    def save_consultation(self, record: ConsultationRecord) -> ConsultationRecord: ...
    def list_consultations(self, thread_id: UUID | str) -> list[ConsultationRecord]: ...
    def save_task(self, record: InterviewTaskProposal) -> InterviewTaskProposal: ...
    def list_tasks(self, thread_id: UUID | str | None = None) -> list[InterviewTaskProposal]: ...


def make_interview_store(json_path: Path) -> InterviewStoreLike:
    backend = (os.environ.get("MINIONS_STORE_BACKEND") or "").lower()
    if backend == "postgres":
        from minions.spokesperson.store_postgres import PostgresInterviewStore

        return PostgresInterviewStore()
    if backend == "json":
        from minions.spokesperson.store import InterviewStore

        return InterviewStore(json_path)
    if has_database_url():
        from minions.spokesperson.store_postgres import PostgresInterviewStore

        return PostgresInterviewStore()
    from minions.spokesperson.store import InterviewStore

    return InterviewStore(json_path)
