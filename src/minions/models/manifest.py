"""Project manifest — declares a managed project's identity, budget, team, and policies."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class ManifestSource(BaseModel):
    """Where the project lives. Either ``path`` (local working tree) or
    ``repo`` (``owner/name``) must be set; if both are provided, ``path``
    is preferred when it exists. Resolution happens in
    :func:`minions.working_tree.resolve_working_tree`.
    """

    kind: Literal["github", "local"]
    path: str | None = None
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


class DossierFreshnessOverrides(BaseModel):
    """Per-project freshness thresholds for ``PROJECT_DOSSIER.md``.

    Planning reads the latest merged dossier and labels it ``ok``/``stale``/
    ``very_stale`` based on these. Defaults match the spec.
    """

    ok_max_age_days: int = 14
    ok_max_commit_drift: int = 200
    stale_max_age_days: int = 30
    stale_max_commit_drift: int = 500


class DossierConfig(BaseModel):
    """Per-project knobs for the agent-authored dossier.

    The dossier itself is generated and maintained by the discoverer crew; this
    block only configures publication target and the rate-limit on agent-
    proposed GitHub issues. See ``openspec/changes/project-dossier-and-
    grounded-planning/``.
    """

    publish: bool = True
    max_new_issues_per_cycle: int = 5
    freshness_overrides: DossierFreshnessOverrides = Field(
        default_factory=DossierFreshnessOverrides
    )

    @field_validator("max_new_issues_per_cycle")
    @classmethod
    def _cap_must_be_positive(cls, v: int) -> int:
        if v < 0:
            raise ValueError("max_new_issues_per_cycle must be >= 0")
        return v


class HeadcountLimits(BaseModel):
    engineers: int = 8
    senior_engineers: int = 4
    qa: int = 3
    architects: int = 2
    total_team_size: int = 20


class HealthCheck(BaseModel):
    """One probe against the deployed site after a merge."""

    path: str  # "/" or "/api/health"
    expect_status: int = 200
    expect_body_contains: str | None = None
    timeout_seconds: float = 10.0


class DeployConfig(BaseModel):
    """Per-project post-deploy verification config.

    ``target=none`` skips verification entirely (used for projects that
    do not deploy on every merge — pure libraries, docs-only repos, etc.).
    For ``target=vercel``, the orchestrator polls the Vercel API for the
    deployment matching the merge sha, then probes ``production_url``
    via each ``health_checks`` entry. On any failed check, files a
    ``risk=high`` Decision proposing rollback.
    """

    target: str = "none"  # "vercel" | "generic" | "none"
    production_url: str | None = None
    health_checks: list[HealthCheck] = Field(default_factory=list)
    # When True the verifier also fetches the first N <img src="…"> URLs
    # off the production_url home page and checks each returns 2xx.
    # Catches the next/image-optimizer outage class without a headless
    # browser.
    check_image_assets: bool = True
    max_image_assets: int = 5
    max_wait_minutes: int = 15


class FlowControl(BaseModel):
    """Per-project flow-control knobs that guard token spend + GitHub noise.

    `max_open_prs` caps the number of distinct open minions-authored PRs the
    sweep is allowed to leave outstanding for one project at once. Default
    is 5; the operator can lift it per project in the manifest. When the
    cap is hit, the next ``execute-approved`` and ``pr-followup`` sweeps
    refuse to queue new work for that project until existing PRs are
    merged or closed.
    """

    max_open_prs: int = 5

    # Max in-place retries the owner sweep is allowed against ONE PR before
    # escalating to the operator. After the cap, no more engineer-crew
    # dispatches fire for that PR until the operator answers the escalation
    # Question Record. Keeps a stuck PR from burning the project's monthly
    # budget on infinite retries.
    max_retries_per_pr: int = 3


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

    dossier: DossierConfig = Field(default_factory=DossierConfig)
    flow_control: FlowControl = Field(default_factory=FlowControl)
    preflight: PreflightConfig = Field(default_factory=lambda: _default_preflight())
    deploy: DeployConfig = Field(default_factory=DeployConfig)

    @field_validator("agents", mode="before")
    @classmethod
    def _coerce_agents(cls, v: Any) -> Any:
        # YAML parses an empty mapping with only commented children as None.
        return {} if v is None else v


def _default_preflight() -> PreflightConfig:
    # Local import to avoid a cycle: preflight.models imports nothing from
    # manifest, and manifest only needs the type at field-construction time.
    from minions.preflight.models import PreflightConfig

    return PreflightConfig()


# Resolve the forward reference so Pydantic can construct Manifest instances
# without callers having to call model_rebuild() themselves. Mirrors the
# pattern used for DossierDigest at the bottom of onboarding/profile.py.
from minions.preflight.models import PreflightConfig  # noqa: E402, F401

Manifest.model_rebuild()


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
