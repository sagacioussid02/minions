"""LLM factory mapping a ModelTier to a CrewAI LLM instance.

CrewAI's native ``LLM`` class wraps LiteLLM under the hood, so model names
take the form ``<provider>/<model-id>``. We use Anthropic's published model
ids verbatim.
"""

from __future__ import annotations

from crewai import LLM

from minions.models.roles import (
    CadenceProfile,
    ModelTier,
    Role,
    resolve_model_id,
)


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


def llm_for_role(
    role: Role,
    *,
    api_key: str,
    cadence: CadenceProfile = "v0_frugal",
    tier: ModelTier | None = None,
    overrides: dict[Role, ModelTier] | None = None,
    max_tokens: int = 4096,
    temperature: float = 0.3,
) -> LLM:
    """Build a CrewAI LLM for a role, honoring the model registry.

    ``tier`` is the role's already-resolved tier (e.g. from a ``MinionAgent``,
    which bakes in manifest overrides); a ``MODEL_<ROLE>`` env var still wins
    over it. Defaults preserve current behavior exactly.
    """
    return LLM(
        model=f"anthropic/{resolve_model_id(role, cadence=cadence, overrides=overrides, tier=tier)}",
        api_key=api_key,
        max_tokens=max_tokens,
        temperature=temperature,
    )
