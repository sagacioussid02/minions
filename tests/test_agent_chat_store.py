"""Tests for the JSON-backed AgentChatStore (Surface B)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from minions.agent_chat.store import AgentChatStore
from minions.models.agent_chat import AgentChatMessage, AgentChatThread


def _thread(**kwargs) -> AgentChatThread:
    base = {"agent_id": "engineer@Demo#1", "project": "Demo"}
    base.update(kwargs)
    return AgentChatThread.model_validate(base)


def _message(thread_id, **kwargs) -> AgentChatMessage:
    base = {"thread_id": thread_id, "role": "user", "content": "hello"}
    base.update(kwargs)
    return AgentChatMessage.model_validate(base)


def test_save_and_get_thread_round_trip(tmp_path: Path) -> None:
    store = AgentChatStore(tmp_path / "agent_chat.json")
    thread = store.save_thread(_thread(title="Friday postmortem"))

    fetched = store.get_thread(thread.id)
    assert fetched is not None
    assert fetched.title == "Friday postmortem"
    assert fetched.agent_id == "engineer@Demo#1"


def test_list_threads_for_agent_orders_by_last_message_desc(tmp_path: Path) -> None:
    store = AgentChatStore(tmp_path / "agent_chat.json")
    older = store.save_thread(_thread(last_message_at=datetime(2026, 5, 1, tzinfo=UTC)))
    newer = store.save_thread(_thread(last_message_at=datetime(2026, 5, 20, tzinfo=UTC)))
    store.save_thread(_thread(agent_id="engineer@OtherProject", title="unrelated"))

    threads = store.list_threads_for_agent("engineer@Demo#1")
    assert [t.id for t in threads] == [newer.id, older.id]


def test_save_message_bumps_thread_last_message_at(tmp_path: Path) -> None:
    store = AgentChatStore(tmp_path / "agent_chat.json")
    thread = store.save_thread(_thread(last_message_at=datetime(2026, 5, 1, tzinfo=UTC)))
    later = datetime(2026, 5, 27, 12, 0, tzinfo=UTC)
    store.save_message(_message(thread.id, content="hey", created_at=later))

    refreshed = store.get_thread(thread.id)
    assert refreshed is not None
    assert refreshed.last_message_at == later


def test_list_messages_filters_and_orders_chronologically(tmp_path: Path) -> None:
    store = AgentChatStore(tmp_path / "agent_chat.json")
    thread = store.save_thread(_thread())
    other_thread = store.save_thread(_thread(title="other"))

    earlier = _message(thread.id, content="first", created_at=datetime(2026, 5, 1, tzinfo=UTC))
    later = _message(
        thread.id,
        role="agent",
        content="second",
        created_at=datetime(2026, 5, 2, tzinfo=UTC),
    )
    noise = _message(other_thread.id, content="elsewhere")
    store.save_message(later)
    store.save_message(earlier)
    store.save_message(noise)

    msgs = store.list_messages(thread.id)
    assert [m.content for m in msgs] == ["first", "second"]


def test_get_thread_returns_none_for_unknown_id(tmp_path: Path) -> None:
    store = AgentChatStore(tmp_path / "agent_chat.json")
    assert store.get_thread(uuid4()) is None
