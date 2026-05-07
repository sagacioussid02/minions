"""Role registry, model tiers, and role -> tier resolution."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal


class ModelTier(StrEnum):
    """Anthropic model tiers used across the org."""

    OPUS = "claude-opus-4-7"
    SONNET = "claude-sonnet-4-6"
    HAIKU = "claude-haiku-4-5-20251001"


class Role(StrEnum):
    # Executive layer
    CEO = "ceo"
    CTO = "cto"
    MD = "managing_director"
    ORG_OWNER = "org_owner"

    # Specialist layer (shared across all projects)
    CLOUD_DEVOPS = "cloud_devops"
    DEVSECOPS = "devsecops"
    TEAM_ARCHITECT = "team_architect"

    # Per-project team
    PRODUCT_OWNER = "product_owner"
    MANAGER = "manager"
    PRINCIPAL = "principal_engineer"
    TTL = "tech_team_lead"
    SR_ENGINEER = "senior_engineer"
    ENGINEER = "engineer"
    INTERN = "intern"
    SR_DEVOPS = "senior_devops"
    SECURITY_CHAMPION = "security_champion"

    # Audit & Challenge layer (independent — reports to operator)
    CHIEF_AUDITOR = "chief_auditor"
    PROCESS_AUDITOR = "process_auditor"
    CODE_AUDITOR = "code_auditor"
    COST_AUDITOR = "cost_auditor"
    DEVILS_ADVOCATE = "devils_advocate"

    # Extended catalog (added on demand via team_composition Decision Records)
    QA_ENGINEER = "qa_engineer"
    TEST_ARCHITECT = "test_architect"
    PERFORMANCE_ENGINEER = "performance_engineer"
    DATA_ENGINEER = "data_engineer"
    DOCUMENTATION_ENGINEER = "documentation_engineer"


# Target tier mapping (full-budget end-state).
TARGET_TIER: dict[Role, ModelTier] = {
    Role.CEO: ModelTier.OPUS,
    Role.CTO: ModelTier.OPUS,
    Role.MD: ModelTier.OPUS,
    Role.ORG_OWNER: ModelTier.SONNET,
    Role.CLOUD_DEVOPS: ModelTier.SONNET,
    Role.DEVSECOPS: ModelTier.SONNET,
    Role.TEAM_ARCHITECT: ModelTier.OPUS,
    Role.PRODUCT_OWNER: ModelTier.SONNET,
    Role.MANAGER: ModelTier.SONNET,
    Role.PRINCIPAL: ModelTier.OPUS,
    Role.TTL: ModelTier.SONNET,
    Role.SR_ENGINEER: ModelTier.SONNET,
    Role.ENGINEER: ModelTier.HAIKU,
    Role.INTERN: ModelTier.HAIKU,
    Role.SR_DEVOPS: ModelTier.SONNET,
    Role.SECURITY_CHAMPION: ModelTier.SONNET,
    Role.CHIEF_AUDITOR: ModelTier.SONNET,
    Role.PROCESS_AUDITOR: ModelTier.HAIKU,
    Role.CODE_AUDITOR: ModelTier.SONNET,
    Role.COST_AUDITOR: ModelTier.HAIKU,
    Role.DEVILS_ADVOCATE: ModelTier.SONNET,
    Role.QA_ENGINEER: ModelTier.HAIKU,
    Role.TEST_ARCHITECT: ModelTier.SONNET,
    Role.PERFORMANCE_ENGINEER: ModelTier.SONNET,
    Role.DATA_ENGINEER: ModelTier.SONNET,
    Role.DOCUMENTATION_ENGINEER: ModelTier.HAIKU,
}


# v0 frugal override — drops Opus to Sonnet for cost discipline ($15-30/mo total).
V0_FRUGAL_OVERRIDE: dict[Role, ModelTier] = {
    role: (ModelTier.SONNET if tier is ModelTier.OPUS else tier)
    for role, tier in TARGET_TIER.items()
}


CadenceProfile = Literal["v0_frugal", "v1_balanced", "v2_full"]


def resolve_tier(
    role: Role,
    cadence: CadenceProfile = "v0_frugal",
    overrides: dict[Role, ModelTier] | None = None,
) -> ModelTier:
    """Resolve the model tier for a role under a cadence profile.

    Precedence: per-role override > cadence profile > target spec.
    """
    if overrides and role in overrides:
        return overrides[role]
    if cadence == "v0_frugal":
        return V0_FRUGAL_OVERRIDE[role]
    return TARGET_TIER[role]
