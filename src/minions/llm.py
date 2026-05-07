"""LLM factory mapping a ModelTier to a CrewAI LLM instance.

CrewAI's native ``LLM`` class wraps LiteLLM under the hood, so model names
take the form ``<provider>/<model-id>``. We use Anthropic's published model
ids verbatim.
"""

from __future__ import annotations

from crewai import LLM

from minions.models.roles import ModelTier


def llm_for_tier(
    tier: ModelTier,
    *,
    api_key: str,
    max_tokens: int = 4096,
    temperature: float = 0.3,
) -> LLM:
    """Build a CrewAI LLM for the given Anthropic tier."""
    return LLM(
        model=f"anthropic/{tier.value}",
        api_key=api_key,
        max_tokens=max_tokens,
        temperature=temperature,
    )
