"""MinionAgent → crewai.Agent translation.

Keeps CrewAI imports out of the orchestrator-side ``MinionAgent`` so tests
that don't exercise the LLM stay fast and provider-agnostic.
"""

from __future__ import annotations

from crewai import Agent

from minions.agents.base import MinionAgent
from minions.llm import llm_for_tier


def make_crewai_agent(
    agent: MinionAgent,
    *,
    api_key: str,
    max_tokens: int | None = None,
) -> Agent:
    """Translate a MinionAgent config into a runnable CrewAI Agent.

    The safety preamble is injected into the backstory so it is part of the
    system prompt for every call this agent makes. If the MinionAgent has a
    ``display_name`` set, the CrewAI ``role`` becomes ``"<Name>, <Role>"`` so
    the model self-identifies as a person.

    ``max_tokens`` overrides the default LLM output cap. Pass a higher value
    for crews that emit large structured payloads (engineer crew → file
    contents; TTL review → long review markdown). The default is enough for
    short reviews / critiques.
    """
    backstory = f"{agent.backstory}\n\n{agent.system_prompt}"
    pretty_role = agent.role.value.replace("_", " ").title()
    role_str = f"{agent.display_name}, {pretty_role}" if agent.display_name else pretty_role
    llm_kwargs: dict[str, object] = {"api_key": api_key}
    if max_tokens is not None:
        llm_kwargs["max_tokens"] = max_tokens
    return Agent(
        role=role_str,
        goal=agent.goal,
        backstory=backstory,
        llm=llm_for_tier(agent.tier, **llm_kwargs),  # type: ignore[arg-type]
        allow_delegation=False,
        verbose=False,
        max_iter=3,  # cost-discipline cap
    )
