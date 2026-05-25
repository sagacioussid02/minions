"""AgentLearningStore backend selector."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol
from uuid import UUID

from minions.db.connection import has_database_url
from minions.models.learning import AgentLearningRecord, LearningKind


class AgentLearningStoreLike(Protocol):
    def save(self, record: AgentLearningRecord) -> AgentLearningRecord: ...
    def update(self, record: AgentLearningRecord) -> AgentLearningRecord: ...
    def get(self, record_id: UUID | str) -> AgentLearningRecord | None: ...
    def list_all(self, include_inactive: bool = False) -> list[AgentLearningRecord]: ...
    def list_by_agent(
        self,
        agent_id: str,
        include_inactive: bool = False,
    ) -> list[AgentLearningRecord]: ...
    def list_relevant(
        self,
        *,
        role: str | None = None,
        project: str | None = None,
        kind: LearningKind | None = None,
        limit: int = 10,
        include_global: bool = True,
    ) -> list[AgentLearningRecord]: ...
    def mark_used(self, record_id: UUID | str) -> AgentLearningRecord | None: ...
    def supersede(
        self,
        old_record_id: UUID | str,
        new_record_id: UUID | str,
    ) -> AgentLearningRecord | None: ...


def make_agent_learning_store(json_path: Path) -> AgentLearningStoreLike:
    backend = (os.environ.get("MINIONS_STORE_BACKEND") or "").lower()
    if backend == "postgres":
        from minions.learning.store_postgres import PostgresAgentLearningStore

        return PostgresAgentLearningStore()
    if backend == "json":
        from minions.learning.store import AgentLearningStore

        return AgentLearningStore(json_path)
    if has_database_url():
        from minions.learning.store_postgres import PostgresAgentLearningStore

        return PostgresAgentLearningStore()
    from minions.learning.store import AgentLearningStore

    return AgentLearningStore(json_path)
