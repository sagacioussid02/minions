"""AgentChat store backend selector. Same pattern as ``approval/store_factory``."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol
from uuid import UUID

from minions.db.connection import has_database_url
from minions.models.agent_chat import AgentChatMessage, AgentChatThread


class AgentChatStoreLike(Protocol):
    def save_thread(self, thread: AgentChatThread) -> AgentChatThread: ...
    def get_thread(self, thread_id: UUID | str) -> AgentChatThread | None: ...
    def list_threads_for_agent(self, agent_id: str) -> list[AgentChatThread]: ...
    def save_message(self, msg: AgentChatMessage) -> AgentChatMessage: ...
    def list_messages(self, thread_id: UUID | str) -> list[AgentChatMessage]: ...


def make_agent_chat_store(json_path: Path) -> AgentChatStoreLike:
    """Pick an AgentChatStore backend per env. ``json_path`` is the JSON fallback."""
    backend = (os.environ.get("MINIONS_STORE_BACKEND") or "").lower()
    if backend == "postgres":
        from minions.agent_chat.store_postgres import PostgresAgentChatStore

        return PostgresAgentChatStore()
    if backend == "json":
        from minions.agent_chat.store import AgentChatStore

        return AgentChatStore(json_path)
    if has_database_url():
        from minions.agent_chat.store_postgres import PostgresAgentChatStore

        return PostgresAgentChatStore()
    from minions.agent_chat.store import AgentChatStore

    return AgentChatStore(json_path)
