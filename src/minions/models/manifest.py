"""Project manifest — declares a managed project's identity, budget, team, and policies."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class ManifestSource(BaseModel):
    kind: Literal["github", "local"]
    path: str
    repo: str | None = None
    default_branch: str = "main"


class TeamOverrides(BaseModel):
    model_config = ConfigDict(extra="allow")

    intern: bool = True
    engineers: int = 3
    enable_security_champion: bool = True


class TierOverrides(BaseModel):
    model_config = ConfigDict(extra="allow")

    default: str | None = None


class AutoApproveRule(BaseModel):
    cls: str = Field(alias="class")
    max_risk: Literal["low", "medium", "high"] = "low"

    model_config = ConfigDict(populate_by_name=True)


class NeverAutoApproveRule(BaseModel):
    cls: str = Field(alias="class")

    model_config = ConfigDict(populate_by_name=True)


class ApprovalChannels(BaseModel):
    email: bool = True
    github_pr: bool = True
    local_branch_prefix: str | None = None


class DeliveryTargets(BaseModel):
    scope: Literal["portfolio", "project"] = "portfolio"
    share_weight: float = 1.0
    features_per_week: float | None = None
    bugs_per_week: float | None = None
    tech_debt_per_week: float | None = None


class HeadcountLimits(BaseModel):
    engineers: int = 8
    senior_engineers: int = 4
    qa: int = 3
    architects: int = 2
    total_team_size: int = 20


class Manifest(BaseModel):
    """Per-project manifest, loaded from minions/projects/<name>.yaml."""

    name: str
    description: str
    source: ManifestSource

    weekly_budget_usd: float
    monthly_budget_usd: float

    cadence_profile: Literal["v0_frugal", "v1_balanced", "v2_full"] = "v0_frugal"

    delivery_targets: DeliveryTargets = Field(default_factory=DeliveryTargets)

    team: TeamOverrides = Field(default_factory=TeamOverrides)
    headcount_limits: HeadcountLimits | None = None
    tier_overrides: TierOverrides = Field(default_factory=TierOverrides)

    auto_approve: list[AutoApproveRule] = Field(default_factory=list)
    never_auto_approve: list[NeverAutoApproveRule] = Field(default_factory=list)

    approval_channels: ApprovalChannels = Field(default_factory=ApprovalChannels)

    # Optional display-name registry: maps role.value → name (single-seat) or list of names (multi-seat).
    # Used in CLI, approval notifications, PR comments, CrewAI Agent role strings.
    # Excess seats beyond the named list fall back to <role>@<project>#<idx>.
    agents: dict[str, str | list[str]] = Field(default_factory=dict)

    owner: str

    risk_thresholds: dict[str, Any] | None = None

    @field_validator("agents", mode="before")
    @classmethod
    def _coerce_agents(cls, v: Any) -> Any:
        # YAML parses an empty mapping with only commented children as None.
        return {} if v is None else v


def load_manifest(path: Path) -> Manifest:
    """Load and validate a project manifest from YAML."""
    raw = yaml.safe_load(path.read_text())
    try:
        return Manifest.model_validate(raw)
    except ValidationError as e:
        raise ValueError(f"manifest at {path} failed validation:\n{e}") from e


def load_active_manifests(projects_dir: Path) -> dict[str, Manifest]:
    """Load every active manifest in projects_dir.

    Skips entries whose parent directory begins with `_` (e.g., `_deferred/`).
    Sorted by file name for deterministic ordering.
    """
    manifests: dict[str, Manifest] = {}
    for yaml_path in sorted(projects_dir.glob("*.yaml")):
        if yaml_path.parent.name.startswith("_"):
            continue
        m = load_manifest(yaml_path)
        manifests[m.name] = m
    return manifests
