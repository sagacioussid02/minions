"""Model registry: ROLE -> model ID, overridable per role via MODEL_<ROLE> env vars.

Covers src/minions/models/roles.py (resolve_model_id), src/minions/llm.py
(llm_for_role), and the wiring through src/minions/crews/factory.py — the
single place CrewAI Agents get their LLM.
"""

from minions.agents.base import MinionAgent
from minions.crews import factory
from minions.llm import llm_for_role
from minions.models.roles import (
    ModelTier,
    Role,
    model_env_var,
    resolve_model_id,
)


def test_defaults_preserve_current_models():
    # v0 frugal defaults: Opus roles drop to Sonnet, engineers stay Haiku.
    assert resolve_model_id(Role.CEO) == "claude-sonnet-4-6"
    assert resolve_model_id(Role.PRINCIPAL) == "claude-sonnet-4-6"
    assert resolve_model_id(Role.ENGINEER) == "claude-haiku-4-5-20251001"
    assert resolve_model_id(Role.INTERN) == "claude-haiku-4-5-20251001"
    # v2 full returns the target tier.
    assert resolve_model_id(Role.CEO, cadence="v2_full") == "claude-opus-4-7"


def test_model_env_var_name():
    assert model_env_var(Role.PRINCIPAL) == "MODEL_PRINCIPAL_ENGINEER"
    assert model_env_var(Role.ENGINEER) == "MODEL_ENGINEER"
    assert model_env_var(Role.DEVILS_ADVOCATE) == "MODEL_DEVILS_ADVOCATE"


def test_env_override_by_tier_nickname(monkeypatch):
    monkeypatch.setenv("MODEL_ENGINEER", "opus")
    assert resolve_model_id(Role.ENGINEER) == "claude-opus-4-7"


def test_env_override_by_full_model_id(monkeypatch):
    monkeypatch.setenv("MODEL_ENGINEER", "claude-sonnet-4-6")
    assert resolve_model_id(Role.ENGINEER) == "claude-sonnet-4-6"


def test_env_override_beats_manifest_override(monkeypatch):
    monkeypatch.setenv("MODEL_PRINCIPAL_ENGINEER", "haiku")
    overrides = {Role.PRINCIPAL: ModelTier.OPUS}
    assert resolve_model_id(Role.PRINCIPAL, overrides=overrides) == "claude-haiku-4-5-20251001"


def test_env_override_beats_explicit_tier(monkeypatch):
    monkeypatch.setenv("MODEL_ENGINEER", "sonnet")
    assert resolve_model_id(Role.ENGINEER, tier=ModelTier.HAIKU) == "claude-sonnet-4-6"


def test_blank_env_value_is_ignored(monkeypatch):
    monkeypatch.setenv("MODEL_ENGINEER", "   ")
    assert resolve_model_id(Role.ENGINEER) == "claude-haiku-4-5-20251001"


def test_llm_for_role_uses_env_flipped_model(monkeypatch):
    monkeypatch.setenv("MODEL_ENGINEER", "opus")
    llm = llm_for_role(Role.ENGINEER, api_key="sk-ant-test")
    assert llm.model == "claude-opus-4-7"


def test_factory_wires_env_override_to_crewai_agent(monkeypatch):
    monkeypatch.setenv("MODEL_ENGINEER", "opus")
    agent = MinionAgent.for_role(Role.ENGINEER, project="demo")
    crewai_agent = factory.make_crewai_agent(agent, api_key="sk-ant-test")
    assert crewai_agent.llm.model == "claude-opus-4-7"


def test_factory_default_model_unchanged_without_env():
    agent = MinionAgent.for_role(Role.ENGINEER, project="demo")
    crewai_agent = factory.make_crewai_agent(agent, api_key="sk-ant-test")
    assert crewai_agent.llm.model == "claude-haiku-4-5-20251001"
