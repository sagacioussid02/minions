"""MinionAgent → crewai.Agent translation.

Keeps CrewAI imports out of the orchestrator-side ``MinionAgent`` so tests
that don't exercise the LLM stay fast and provider-agnostic.
"""

from __future__ import annotations

from crewai import Agent

from minions.agents.base import MinionAgent
from minions.llm import llm_for_tier


def make_crewai_agent(agent: MinionAgent, *, api_key: str) -> Agent:
    """Translate a MinionAgent config into a runnable CrewAI Agent.

    The safety preamble is injected into the backstory so it is part of the
    system prompt for every call this agent makes. If the MinionAgent has a
    ``display_name`` set, the CrewAI ``role`` becomes ``"<Name>, <Role>"`` so
    the model self-identifies as a person.
    """
    backstory = f"{agent.backstory}\n\n{agent.system_prompt}"
    pretty_role = agent.role.value.replace("_", " ").title()
    role_str = f"{agent.display_name}, {pretty_role}" if agent.display_name else pretty_role
    return Agent(
        role=role_str,
        goal=agent.goal,
        backstory=backstory,
        llm=llm_for_tier(agent.tier, api_key=api_key),
        allow_delegation=False,
        verbose=False,
        max_iter=3,  # cost-discipline cap
    )
