"""AgileStore backend selector."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol
from uuid import UUID

from minions.db.connection import has_database_url
from minions.models.agile import AgileRitualRecord, PMAnswerRecord


class AgileStoreLike(Protocol):
    def save_ritual(self, record: AgileRitualRecord) -> AgileRitualRecord: ...
    def list_rituals(self, project: str | None = None) -> list[AgileRitualRecord]: ...
    def get_ritual(self, record_id: UUID | str) -> AgileRitualRecord | None: ...
    def save_pm_answer(self, record: PMAnswerRecord) -> PMAnswerRecord: ...
    def list_pm_answers(self, project: str | None = None) -> list[PMAnswerRecord]: ...


def make_agile_store(json_path: Path) -> AgileStoreLike:
    backend = (os.environ.get("MINIONS_STORE_BACKEND") or "").lower()
    if backend == "postgres":
        from minions.agile.store_postgres import PostgresAgileStore

        return PostgresAgileStore()
    if backend == "json":
        from minions.agile.store import AgileStore

        return AgileStore(json_path)
    if has_database_url():
        from minions.agile.store_postgres import PostgresAgileStore

        return PostgresAgileStore()
    from minions.agile.store import AgileStore

    return AgileStore(json_path)
