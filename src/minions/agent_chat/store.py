"""JSON-backed AgentChatStore. Same pattern as QuestionStore."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

from minions.models.agent_chat import AgentChatMessage, AgentChatThread


class AgentChatStore:
    """JSON file at ``data/local/agent_chat.json`` with two top-level keys.

    Layout::

        {
          "threads":  {"<thread_id>": {...AgentChatThread...}, ...},
          "messages": {"<message_id>": {...AgentChatMessage...}, ...}
        }
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text(json.dumps({"threads": {}, "messages": {}}))

    def _load_all(self) -> dict[str, dict[str, dict[str, object]]]:
        text = self.path.read_text()
        if not text.strip():
            return {"threads": {}, "messages": {}}
        data: dict[str, dict[str, dict[str, object]]] = json.loads(text)
        data.setdefault("threads", {})
        data.setdefault("messages", {})
        return data

    def _save_all(self, data: dict[str, dict[str, dict[str, object]]]) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, default=str))
        tmp.replace(self.path)

    # --- threads -------------------------------------------------------------

    def save_thread(self, thread: AgentChatThread) -> AgentChatThread:
        data = self._load_all()
        data["threads"][str(thread.id)] = thread.model_dump(mode="json")
        self._save_all(data)
        return thread

    def get_thread(self, thread_id: UUID | str) -> AgentChatThread | None:
        raw = self._load_all()["threads"].get(str(thread_id))
        if raw is None:
            return None
        return AgentChatThread.model_validate(raw)

    def list_threads_for_agent(self, agent_id: str) -> list[AgentChatThread]:
        threads = [
            AgentChatThread.model_validate(raw) for raw in self._load_all()["threads"].values()
        ]
        threads = [t for t in threads if t.agent_id == agent_id]
        threads.sort(key=lambda t: t.last_message_at, reverse=True)
        return threads

    # --- messages ------------------------------------------------------------

    def save_message(self, msg: AgentChatMessage) -> AgentChatMessage:
        data = self._load_all()
        data["messages"][str(msg.id)] = msg.model_dump(mode="json")
        # Bump the parent thread's last_message_at if it exists.
        thread_raw = data["threads"].get(str(msg.thread_id))
        if thread_raw is not None:
            thread_raw["last_message_at"] = msg.created_at.isoformat()
            data["threads"][str(msg.thread_id)] = thread_raw
        self._save_all(data)
        return msg

    def list_messages(self, thread_id: UUID | str) -> list[AgentChatMessage]:
        msgs = [
            AgentChatMessage.model_validate(raw)
            for raw in self._load_all()["messages"].values()
            if str(raw.get("thread_id")) == str(thread_id)
        ]
        msgs.sort(key=lambda m: m.created_at)
        return msgs
