"""Tests for the crew team-roster preamble (Python twin of the web chat)."""

from __future__ import annotations

from pathlib import Path

from minions.agents.base import MinionAgent
from minions.agents.roster import build_project_agents
from minions.agents.team import team_roster_preamble
from minions.models.manifest import load_manifest
from minions.models.roles import Role

REPO_ROOT = Path(__file__).resolve().parents[1]


def _demo_agent(role: Role) -> MinionAgent:
    manifest = load_manifest(REPO_ROOT / "projects" / "demo.yaml")
    agents = build_project_agents(manifest)
    return next(a for a in agents if a.role is role)


def test_preamble_lists_project_peers_and_leadership():
    po = _demo_agent(Role.PRODUCT_OWNER)
    text = team_roster_preamble(po)

    assert text.startswith("# Your team")
    # Leadership seats appear and are labelled as such.
    assert "(leadership)" in text
    assert "ceo" in text
    # Project peers from the same project appear, labelled with the project.
    assert "(project Demo)" in text
    assert "engineer" in text


def test_preamble_excludes_self():
    po = _demo_agent(Role.PRODUCT_OWNER)
    text = team_roster_preamble(po)
    # The agent should not list itself as a teammate.
    assert f"- {po.label} — product_owner" not in text


def test_preamble_is_empty_for_unknown_project():
    # An agent on a project with no manifest still resolves leadership, but a
    # totally unresolvable agent yields no rows. Use a project that isn't on
    # disk and a role; leadership still resolves, so we assert it degrades
    # gracefully rather than raising.
    ghost = MinionAgent.for_role(Role.ENGINEER, project="does-not-exist")
    text = team_roster_preamble(ghost)
    # Never raises; either empty or leadership-only, but no project peers.
    assert "(project does-not-exist)" not in text
