"""Agent memory store backend selector."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

from minions.db.connection import has_database_url
from minions.models.agent_memory import AgentMemoryRecord


class AgentMemoryStoreLike(Protocol):
    def save(self, record: AgentMemoryRecord) -> AgentMemoryRecord: ...
    def record(self, **kwargs: Any) -> AgentMemoryRecord: ...
    def get(self, record_id: UUID | str) -> AgentMemoryRecord | None: ...
    def list_all(self) -> list[AgentMemoryRecord]: ...
    def list_hot(self, agent_id: str, *, char_cap: int = 5000) -> list[AgentMemoryRecord]: ...
    def list_by_agent(
        self,
        agent_id: str,
        *,
        include_cold: bool = False,
    ) -> list[AgentMemoryRecord]: ...
    def demote_hot_older_than(self, current_by_project: dict[str, int]) -> int: ...


def make_agent_memory_store(json_path: Path) -> AgentMemoryStoreLike:
    backend = (os.environ.get("MINIONS_STORE_BACKEND") or "").lower()
    if backend == "postgres":
        from minions.agents.memory_store_postgres import PostgresAgentMemoryStore

        return PostgresAgentMemoryStore()
    if backend == "json":
        from minions.agents.memory_store import AgentMemoryStore

        return AgentMemoryStore(json_path)
    if has_database_url():
        from minions.agents.memory_store_postgres import PostgresAgentMemoryStore

        return PostgresAgentMemoryStore()
    from minions.agents.memory_store import AgentMemoryStore

    return AgentMemoryStore(json_path)
