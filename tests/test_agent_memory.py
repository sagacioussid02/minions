from __future__ import annotations

from pathlib import Path

from minions.agents.base import MinionAgent
from minions.agents.memory import recent_work_preamble
from minions.agents.memory_store import AgentMemoryStore
from minions.crews import factory
from minions.models.agent_memory import AgentMemoryRecord
from minions.models.roles import ModelTier, Role
from minions.scheduled.agent_memory_demote import run_agent_memory_demote
from minions.sprints.store import SprintCounterStore


def test_list_hot_respects_char_cap(tmp_path: Path) -> None:
    store = AgentMemoryStore(tmp_path / "memory.json")
    store.record(agent_id="engineer@Demo", event="task_done", summary="short")
    store.record(agent_id="engineer@Demo", event="task_done", summary="x" * 200)

    hot = store.list_hot("engineer@Demo", char_cap=20)

    assert len(hot) == 1
    assert len(hot[0].summary) <= 20


def test_demote_hot_to_cold_by_sprint(tmp_path: Path) -> None:
    memory = AgentMemoryStore(tmp_path / "memory.json")
    sprints = SprintCounterStore(tmp_path / "sprints.json")
    sprints.bump("Demo")  # 0
    sprints.bump("Demo")  # 1
    sprints.bump("Demo")  # 2
    old = memory.record(
        agent_id="engineer@Demo",
        sprint_number=0,
        event="task_done",
        summary="old",
    )
    recent = memory.record(
        agent_id="engineer@Demo",
        sprint_number=2,
        event="task_done",
        summary="recent",
    )

    report = run_agent_memory_demote(memory_store=memory, sprint_counter_store=sprints)

    assert report.demoted == 1
    assert memory.get(old.id).tier == "cold"  # type: ignore[union-attr]
    assert memory.get(recent.id).tier == "hot"  # type: ignore[union-attr]
    assert [r.id for r in memory.list_by_agent("engineer@Demo")] == [recent.id]
    assert {r.id for r in memory.list_by_agent("engineer@Demo", include_cold=True)} == {
        old.id,
        recent.id,
    }


def test_recent_work_preamble_empty_for_fresh_agent() -> None:
    assert recent_work_preamble([]) == ""


def test_recent_work_preamble_formats_records() -> None:
    text = recent_work_preamble(
        [
            AgentMemoryRecord(
                agent_id="engineer@Demo",
                event="pr_opened",
                summary="Opened PR for checkout fix.",
                pr_url="https://example/pr/1",
            )
        ]
    )

    assert "Your Recent Work:" in text
    assert "[pr_opened] Opened PR for checkout fix." in text
    assert "https://example/pr/1" in text


def test_make_crewai_agent_injects_recent_work(monkeypatch, tmp_path: Path) -> None:
    memory = AgentMemoryStore(tmp_path / "memory.json")
    memory.record(
        agent_id="engineer@Demo",
        event="task_done",
        summary="Finished checkout validation.",
    )
    captured: dict[str, object] = {}

    class FakeAgent:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(factory, "Agent", FakeAgent)
    monkeypatch.setattr(factory, "llm_for_tier", lambda *_a, **_k: "llm")
    agent = MinionAgent(
        role=Role.ENGINEER,
        name="engineer@Demo",
        project="Demo",
        tier=ModelTier.HAIKU,
        backstory="Engineer.",
        goal="Build.",
    )

    factory.make_crewai_agent(agent, api_key="test", memory_store=memory)

    assert "Your Recent Work:" in str(captured["backstory"])
    assert "Finished checkout validation." in str(captured["backstory"])
