"""Demote older hot agent memories to cold storage."""

from __future__ import annotations

from pydantic import BaseModel

from minions.agents.memory_store_factory import AgentMemoryStoreLike
from minions.sprints.store_factory import SprintCounterStoreLike


class AgentMemoryDemoteReport(BaseModel):
    demoted: int
    projects_seen: int


def run_agent_memory_demote(
    *,
    memory_store: AgentMemoryStoreLike,
    sprint_counter_store: SprintCounterStoreLike,
) -> AgentMemoryDemoteReport:
    counters = sprint_counter_store.list_all()
    current = {
        counter.project: counter.current_sprint_number
        for counter in counters
    }
    return AgentMemoryDemoteReport(
        demoted=memory_store.demote_hot_older_than(current),
        projects_seen=len(current),
    )
