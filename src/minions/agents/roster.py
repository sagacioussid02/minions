"""Roster builder — turns manifests + portfolio config into a list of MinionAgents.

Names come from:
  - Per-project agents:    ``projects/<name>.yaml`` ``agents:`` block
  - Shared (exec / specialist / audit): ``config/portfolio.yaml`` ``named_agents:`` block

Both blocks map ``role.value`` → name (single-seat) or list of names (multi-seat).
Excess seats beyond the named list fall back to ``<role>@<scope>#<idx>``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from minions.agents.base import MinionAgent
from minions.models.roles import CadenceProfile, Role

if TYPE_CHECKING:
    from minions.config.portfolio import PortfolioConfig
    from minions.models.manifest import Manifest


PER_PROJECT_TEMPLATE: list[Role] = [
    Role.PRODUCT_OWNER,
    Role.MANAGER,
    Role.PRINCIPAL,
    Role.TTL,
    Role.SR_ENGINEER,
    Role.SR_ENGINEER,
    Role.ENGINEER,
    Role.ENGINEER,
    Role.ENGINEER,
    Role.INTERN,
    Role.SR_DEVOPS,
    Role.SECURITY_CHAMPION,
]

SHARED_EXECUTIVE: list[Role] = [Role.CEO, Role.CTO, Role.MD, Role.ORG_OWNER]
SHARED_SPECIALIST: list[Role] = [Role.CLOUD_DEVOPS, Role.DEVSECOPS, Role.TEAM_ARCHITECT]
AUDIT: list[Role] = [
    Role.CHIEF_AUDITOR,
    Role.PROCESS_AUDITOR,
    Role.CODE_AUDITOR,
    Role.COST_AUDITOR,
    Role.DEVILS_ADVOCATE,
]


def _names_for(table: dict[str, str | list[str]], role: Role) -> list[str]:
    raw = table.get(role.value)
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw]
    return [str(n) for n in raw]


def project_role_slots(manifest: Manifest) -> list[Role]:
    """Apply the manifest's team overrides to the per-project template.

    Order matches PER_PROJECT_TEMPLATE; multi-seat roles appear consecutively.
    """
    roles = list(PER_PROJECT_TEMPLATE)
    if not manifest.team.intern:
        roles = [r for r in roles if r is not Role.INTERN]
    if not manifest.team.enable_security_champion:
        roles = [r for r in roles if r is not Role.SECURITY_CHAMPION]
    eng_template_count = sum(1 for r in roles if r is Role.ENGINEER)
    keep = manifest.team.engineers
    if keep < eng_template_count:
        new_roles: list[Role] = []
        kept = 0
        for r in roles:
            if r is Role.ENGINEER:
                if kept < keep:
                    new_roles.append(r)
                    kept += 1
            else:
                new_roles.append(r)
        roles = new_roles
    return roles


def build_project_agents(
    manifest: Manifest, cadence: CadenceProfile = "v0_frugal"
) -> list[MinionAgent]:
    """Build the per-project crew with display names from the manifest."""
    roles = project_role_slots(manifest)
    seat_counters: dict[Role, int] = {}
    out: list[MinionAgent] = []
    for role in roles:
        seat = seat_counters.get(role, 0)
        seat_counters[role] = seat + 1
        names = _names_for(manifest.agents, role)
        display_name = names[seat] if seat < len(names) else None
        out.append(
            MinionAgent.for_role(
                role,
                project=manifest.name,
                cadence=cadence,
                manifest=manifest,
                display_name=display_name,
                seat_index=seat,
            )
        )
    return out


def build_shared_agents(
    portfolio: PortfolioConfig,
    layer: list[Role],
    cadence: CadenceProfile = "v0_frugal",
) -> list[MinionAgent]:
    """Build a shared layer (executive / specialist / audit) with names from portfolio config.

    Each role gets at least one seat. Providing a list of names creates one
    seat per name (e.g., 2 Org Owners covering different project pairs).
    """
    out: list[MinionAgent] = []
    for role in layer:
        names = _names_for(portfolio.named_agents, role)
        seats = max(1, len(names))
        for seat in range(seats):
            display_name = names[seat] if seat < len(names) else None
            out.append(
                MinionAgent.for_role(
                    role,
                    project=None,
                    cadence=cadence,
                    display_name=display_name,
                    seat_index=seat,
                )
            )
    return out


def build_named_agent(
    role: Role,
    *,
    project: str | None,
    manifest: Manifest | None = None,
    portfolio: PortfolioConfig | None = None,
    seat: int = 0,
    cadence: CadenceProfile = "v0_frugal",
) -> MinionAgent:
    """Build a single agent, looking up its display name from the right registry."""
    if project is not None and manifest is not None:
        names = _names_for(manifest.agents, role)
    elif portfolio is not None:
        names = _names_for(portfolio.named_agents, role)
    else:
        names = []
    display_name = names[seat] if seat < len(names) else None
    return MinionAgent.for_role(
        role,
        project=project,
        cadence=cadence,
        manifest=manifest,
        display_name=display_name,
        seat_index=seat,
    )
