"""Question Store backend selector. Same pattern as `approval/store_factory`."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol
from uuid import UUID

from minions.db.connection import has_database_url
from minions.models.question import QuestionRecord, QuestionStatus


class QuestionStoreLike(Protocol):
    def save(self, question: QuestionRecord) -> None: ...
    def get(self, question_id: UUID | str) -> QuestionRecord | None: ...
    def list_all(self) -> list[QuestionRecord]: ...
    def list_by_status(self, status: QuestionStatus) -> list[QuestionRecord]: ...


def make_question_store(json_path: Path) -> QuestionStoreLike:
    """Pick a Question Store backend per env. ``json_path`` is the JSON fallback."""
    backend = (os.environ.get("MINIONS_STORE_BACKEND") or "").lower()
    if backend == "postgres":
        from minions.questions.store_postgres import PostgresQuestionStore

        return PostgresQuestionStore()
    if backend == "json":
        from minions.questions.store import QuestionStore

        return QuestionStore(json_path)
    if has_database_url():
        from minions.questions.store_postgres import PostgresQuestionStore

        return PostgresQuestionStore()
    from minions.questions.store import QuestionStore

    return QuestionStore(json_path)
