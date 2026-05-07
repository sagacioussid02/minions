"""Portfolio-level config loader for config/portfolio.yaml."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


class DeliveryCadenceConfig(BaseModel):
    option: Literal["A", "B"] = "A"
    features_per_week: float = 1.0
    bugs_per_week: float = 2.3
    tech_debt_per_week: float = 1.0
    scope: Literal["portfolio", "project"] = "portfolio"


class AllocationConfig(BaseModel):
    policy: Literal["weighted_stochastic", "round_robin"] = "weighted_stochastic"
    no_fabrication: bool = True
    carry_over: bool = True


class BudgetEnvelope(BaseModel):
    monthly_total_floor_usd: float = 15
    monthly_total_ceiling_usd: float = 30
    current_v0_target_usd: float = 14


class CadenceProfile(BaseModel):
    weekly_planning: str | None = None
    weekly_digest: str | None = None
    daily_monitoring: str | None = None
    standups: str | None = None
    on_demand: str | None = None


class AuditTeamConfig(BaseModel):
    chief_auditor: str = "sonnet"
    process_auditor: str = "haiku"
    code_auditor: str = "sonnet"
    cost_auditor: str = "haiku"
    devils_advocate: str = "sonnet"


class AuditSampling(BaseModel):
    pr_code_audit: float = 0.25
    pr_audit_risk_weighted: bool = True
    high_risk_decisions_devils_advocate: float = 1.0
    procurement_cost_audit: float = 1.0
    team_composition_process_audit: float = 1.0
    sprint_process_audit_weekly: bool = True


class AuditConfig(BaseModel):
    enabled: bool = True
    team: AuditTeamConfig = Field(default_factory=AuditTeamConfig)
    reports_to: Literal["operator"] = "operator"
    write_access: Literal[False] = False
    sampling: AuditSampling = Field(default_factory=AuditSampling)
    monthly_budget_usd: float = 2.00


class TeamCompositionConfig(BaseModel):
    enabled: bool = True
    default_headcount_limits: dict[str, int] = Field(
        default_factory=lambda: {
            "engineers": 8,
            "senior_engineers": 4,
            "qa": 3,
            "architects": 2,
            "total_team_size": 20,
        }
    )
    allowed_new_roles: list[str] = Field(default_factory=list)
    cost_coupled_approval: bool = True


class DelegatedCardConfig(BaseModel):
    enabled: bool = False
    monthly_cap_usd: float = 0
    provider: str | None = None


class ProcurementConfig(BaseModel):
    enabled: bool = True
    monthly_budget_usd: float = 0
    delegated_signup_free_tier: bool = False
    delegated_card: DelegatedCardConfig = Field(default_factory=DelegatedCardConfig)
    email_alias_template: str | None = None
    secret_storage: Literal["aws_secrets_manager", "1password_connect"] | None = None
    tos_acceptance_authorized: bool = False
    quarterly_subscription_review: bool = True


class PortfolioConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    delivery_cadence: DeliveryCadenceConfig = Field(default_factory=DeliveryCadenceConfig)
    allocation: AllocationConfig = Field(default_factory=AllocationConfig)
    budget_envelope: BudgetEnvelope = Field(default_factory=BudgetEnvelope)
    cadence_profiles: dict[str, CadenceProfile] = Field(default_factory=dict)
    audit: AuditConfig = Field(default_factory=AuditConfig)
    team_composition: TeamCompositionConfig = Field(default_factory=TeamCompositionConfig)
    role_definitions: dict[str, dict[str, Any]] = Field(default_factory=dict)
    procurement: ProcurementConfig = Field(default_factory=ProcurementConfig)
    # Display names for shared agents (executive, specialist, audit layers).
    # Same shape as per-project manifests' `agents:` field — role → name or list.
    named_agents: dict[str, str | list[str]] = Field(default_factory=dict)
    owner: str
    # YAML parses unquoted ISO dates (e.g. 2026-04-30) into datetime.date; accept either form.
    locked_in_at: date | str | None = None

    @field_validator("named_agents", mode="before")
    @classmethod
    def _coerce_named_agents(cls, v: Any) -> Any:
        # YAML parses an empty mapping with only commented children as None.
        return {} if v is None else v


def load_portfolio_config(path: Path) -> PortfolioConfig:
    """Load and validate the portfolio-level config from YAML."""
    raw = yaml.safe_load(path.read_text())
    return PortfolioConfig.model_validate(raw)
