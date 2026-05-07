"""Base Agent class — orchestrator-side description of an agent in the org.

The CrewAI Agent (or Claude Agent SDK call) is constructed at execution time
from this configuration. Keeping this layer free of CrewAI imports keeps tests
fast and makes the framework boundary easy to swap later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from minions.agents.safety import safety_preamble_for
from minions.models.roles import (
    CadenceProfile,
    ModelTier,
    Role,
    resolve_tier,
)

if TYPE_CHECKING:
    from minions.models.manifest import Manifest


_NAME_TO_TIER: dict[str, ModelTier] = {
    "haiku": ModelTier.HAIKU,
    "sonnet": ModelTier.SONNET,
    "opus": ModelTier.OPUS,
}


@dataclass
class MinionAgent:
    """Configuration for a single agent in the minions org."""

    role: Role
    name: str  # stable id — '<role>@<project>' (or '@org') with '#<seat>' suffix for multi-seat
    project: str | None
    tier: ModelTier
    backstory: str
    goal: str
    tool_allowlist: list[str] = field(default_factory=list)
    cadence_profile: CadenceProfile = "v0_frugal"
    display_name: str | None = None  # human-given name for UI / prompts (e.g., "Tara")
    seat_index: int = 0  # 0 for single-seat or first seat; 1, 2, ... for multi-seat roles

    @property
    def system_prompt(self) -> str:
        """The full system prompt = safety preamble + role/project framing."""
        return safety_preamble_for(role=self.role.value, project=self.project)

    @property
    def label(self) -> str:
        """Human-readable label: display_name if set, otherwise the stable name."""
        return self.display_name or self.name

    @classmethod
    def for_role(
        cls,
        role: Role,
        *,
        project: str | None,
        cadence: CadenceProfile = "v0_frugal",
        manifest: Manifest | None = None,
        display_name: str | None = None,
        seat_index: int = 0,
    ) -> MinionAgent:
        """Build an agent for a given role within a project (or shared if project is None)."""
        overrides = None
        if manifest is not None:
            overrides = _coerce_tier_overrides(
                manifest.tier_overrides.model_dump(exclude_none=True)
            )

        tier = resolve_tier(role, cadence=cadence, overrides=overrides)
        backstory, goal = _role_backstory_and_goal(role, project)
        scope = project if project else "org"
        suffix = f"#{seat_index}" if seat_index > 0 else ""
        name = f"{role.value}@{scope}{suffix}"

        return cls(
            role=role,
            name=name,
            project=project,
            tier=tier,
            backstory=backstory,
            goal=goal,
            cadence_profile=cadence,
            display_name=display_name,
            seat_index=seat_index,
        )


def _coerce_tier_overrides(raw: dict[str, str]) -> dict[Role, ModelTier]:
    """Translate manifest-level string overrides (e.g. principal: sonnet) to typed overrides.

    Unknown role names and unknown tier names are tolerated (skipped) for forward compatibility.
    """
    overrides: dict[Role, ModelTier] = {}
    for role_str, tier_str in raw.items():
        if role_str == "default":
            # The 'default' key is handled by the orchestrator, not here.
            continue
        try:
            role = Role(role_str)
        except ValueError:
            continue
        tier = _NAME_TO_TIER.get(str(tier_str).lower())
        if tier is not None:
            overrides[role] = tier
    return overrides


def _role_backstory_and_goal(role: Role, project: str | None) -> tuple[str, str]:
    """Default backstory + goal for each role.

    Project-specific customization happens at crew-assembly time later.
    """
    scope = f" on the '{project}' project" if project else " across the portfolio"
    backstories: dict[Role, tuple[str, str]] = {
        Role.CEO: ("Strategic leader of the engineering org.", f"Set portfolio direction{scope}."),
        Role.CTO: ("Technical leader of the engineering org.", f"Set technical direction{scope}."),
        Role.MD: ("Operational leader.", f"Coordinate execution{scope}."),
        Role.ORG_OWNER: ("Owner of one or two projects.", f"Drive outcomes{scope}."),
        Role.PRODUCT_OWNER: (
            "Owner of the product backlog.",
            f"Discover, rank, and propose work{scope}.",
        ),
        Role.MANAGER: ("Sprint manager.", f"Plan and report sprints{scope}."),
        Role.PRINCIPAL: ("Principal engineer.", f"Validate feasibility and architecture{scope}."),
        Role.TTL: ("Tech team lead.", f"Review code and mentor engineers{scope}."),
        Role.SR_ENGINEER: ("Senior engineer.", f"Build complex features{scope}."),
        Role.ENGINEER: ("Engineer.", f"Build standard features{scope}."),
        Role.INTERN: ("Intern.", f"Handle small tasks and docs{scope}."),
        Role.SR_DEVOPS: ("Senior DevOps engineer.", f"Run CI/CD and infra{scope}."),
        Role.SECURITY_CHAMPION: (
            "Security champion.",
            f"Champion secure-by-default practices{scope}.",
        ),
        Role.CLOUD_DEVOPS: ("Cloud DevOps specialist.", "Cross-project cloud infra."),
        Role.DEVSECOPS: ("DevSecOps specialist.", "Cross-project security automation."),
        Role.TEAM_ARCHITECT: ("Cross-project architect.", "Cross-project architecture."),
        Role.CHIEF_AUDITOR: (
            "Chief auditor.",
            "Run the audit calendar and findings register; report directly to the operator.",
        ),
        Role.PROCESS_AUDITOR: (
            "Process auditor.",
            "Audit sprint quality and decision rationale.",
        ),
        Role.CODE_AUDITOR: (
            "Code auditor.",
            "Independently re-review sampled merged PRs.",
        ),
        Role.COST_AUDITOR: (
            "Cost auditor.",
            "Challenge cost and procurement decisions; quarterly subscription review.",
        ),
        Role.DEVILS_ADVOCATE: (
            "Devil's Advocate.",
            "Critique high-impact proposals before operator notification.",
        ),
        Role.QA_ENGINEER: ("QA engineer.", f"Test coverage and quality{scope}."),
        Role.TEST_ARCHITECT: ("Test architect.", f"Test strategy{scope}."),
        Role.PERFORMANCE_ENGINEER: (
            "Performance engineer.",
            f"Performance and load testing{scope}.",
        ),
        Role.DATA_ENGINEER: ("Data engineer.", f"Data pipelines and modeling{scope}."),
        Role.DOCUMENTATION_ENGINEER: ("Documentation engineer.", f"Docs quality{scope}."),
    }
    return backstories.get(role, ("Agent in the minions org.", f"Contribute{scope}."))
