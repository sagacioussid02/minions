"""Team-roster preamble — gives a crew agent awareness of its teammates.

Python twin of the web operator-chat "Your team" section
(``web/lib/agent-chat/context.ts``): an agent knows the peers staffed on its
own project plus the executive leadership seats. Sourced from the standing
manifest + portfolio roster (authoritative for the configured org), so a crew
agent can refer to colleagues by name during a run.

Best-effort by design: any failure resolving the roster yields an empty string
so a crew run is never blocked on it.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from minions.agents.roster import (
    SHARED_EXECUTIVE,
    build_project_agents,
    build_shared_agents,
)

if TYPE_CHECKING:
    from minions.agents.base import MinionAgent

MAX_TEAM_MEMBERS = 24


def _repo_root() -> Path:
    # src/minions/agents/team.py -> parents[3] == repo root
    return Path(__file__).resolve().parents[3]


def _line(agent: MinionAgent, where: str) -> str:
    label = agent.display_name or agent.role.value.replace("_", " ").title()
    return f"- {label} — {agent.role.value} ({where})"


def team_roster_preamble(agent: MinionAgent) -> str:
    """Render a ``# Your team`` block for ``agent`` — project peers + leadership.

    Returns ``""`` when the roster can't be resolved (missing config, inactive
    project, etc.) so it never breaks a crew run.
    """
    try:
        members = _collect_team(agent)
    except Exception:  # noqa: BLE001 — roster awareness is best-effort
        return ""
    if not members:
        return ""
    return "\n".join(
        [
            "# Your team",
            "These are the people you work with — your project teammates and "
            "the leadership above you. Refer to them by name when relevant.",
            "",
            *members,
        ]
    )


def _collect_team(agent: MinionAgent) -> list[str]:
    from minions.config.portfolio import load_portfolio_config
    from minions.models.manifest import load_active_manifests

    root = _repo_root()
    # (is_leadership, line) so we can sort leadership-first.
    rows: list[tuple[bool, str]] = []
    seen: set[str] = set()

    # Leadership — shared executive seats.
    portfolio = load_portfolio_config(root / "config" / "portfolio.yaml")
    for ex in build_shared_agents(portfolio, SHARED_EXECUTIVE):
        if ex.name == agent.name or ex.name in seen:
            continue
        seen.add(ex.name)
        rows.append((True, _line(ex, "leadership")))

    # Project peers — the manifest crew for this agent's project.
    if agent.project:
        manifests = load_active_manifests(root / "projects")
        manifest = manifests.get(agent.project) or next(
            (m for k, m in manifests.items() if k.lower() == agent.project.lower()),
            None,
        )
        if manifest is not None:
            for peer in build_project_agents(manifest):
                if peer.name == agent.name or peer.name in seen:
                    continue
                seen.add(peer.name)
                rows.append((False, _line(peer, f"project {agent.project}")))

    # Leadership first, then project peers; stable by label within each group.
    rows.sort(key=lambda r: (not r[0], r[1].lower()))
    return [line for _, line in rows][:MAX_TEAM_MEMBERS]
