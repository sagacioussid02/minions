"""Tests for the roster builder + display-name resolution."""

from __future__ import annotations

from pathlib import Path

import yaml

from minions.agents.roster import (
    AUDIT,
    SHARED_EXECUTIVE,
    build_named_agent,
    build_project_agents,
    build_shared_agents,
    project_role_slots,
)
from minions.config.portfolio import PortfolioConfig
from minions.models.manifest import Manifest, load_manifest
from minions.models.roles import Role

REPO_ROOT = Path(__file__).resolve().parents[1]


def _write_manifest(tmp_path: Path, agents_block: dict) -> Manifest:
    body = {
        "name": "Demo",
        "description": "test",
        "source": {"kind": "github", "path": "/tmp/x", "repo": "owner/repo"},
        "weekly_budget_usd": 1.0,
        "monthly_budget_usd": 4.0,
        "cadence_profile": "v0_frugal",
        "team": {"intern": False, "engineers": 1, "enable_security_champion": True},
        "agents": agents_block,
        "owner": "x@y.com",
    }
    p = tmp_path / "demo.yaml"
    p.write_text(yaml.safe_dump(body))
    return load_manifest(p)


def test_default_roster_has_no_display_names():
    manifest = load_manifest(REPO_ROOT / "projects" / "demo.yaml")
    agents = build_project_agents(manifest)
    assert all(a.display_name is None for a in agents)


def test_seat_index_for_multi_seat_roles():
    manifest = load_manifest(REPO_ROOT / "projects" / "demo.yaml")
    agents = build_project_agents(manifest)
    seniors = [a for a in agents if a.role is Role.SR_ENGINEER]
    assert len(seniors) == 2
    assert seniors[0].seat_index == 0
    assert seniors[1].seat_index == 1
    assert seniors[0].name == "senior_engineer@Demo"
    assert seniors[1].name == "senior_engineer@Demo#1"


def test_names_applied_from_manifest(tmp_path):
    manifest = _write_manifest(
        tmp_path,
        {
            "product_owner": "Tara",
            "senior_engineer": ["Sasha", "Sven"],
            "engineer": "Eli",
        },
    )
    agents = build_project_agents(manifest)
    po = next(a for a in agents if a.role is Role.PRODUCT_OWNER)
    assert po.display_name == "Tara"
    assert po.label == "Tara"

    seniors = [a for a in agents if a.role is Role.SR_ENGINEER]
    assert [a.display_name for a in seniors] == ["Sasha", "Sven"]

    eng = next(a for a in agents if a.role is Role.ENGINEER)
    assert eng.display_name == "Eli"


def test_excess_seats_fall_back_to_unnamed(tmp_path):
    manifest = _write_manifest(tmp_path, {"senior_engineer": ["OnlyOne"]})
    agents = build_project_agents(manifest)
    seniors = [a for a in agents if a.role is Role.SR_ENGINEER]
    assert seniors[0].display_name == "OnlyOne"
    assert seniors[1].display_name is None
    assert seniors[1].label == seniors[1].name  # falls back to stable name


def test_label_uses_display_name_when_set(tmp_path):
    manifest = _write_manifest(tmp_path, {"product_owner": "Tara"})
    agents = build_project_agents(manifest)
    po = next(a for a in agents if a.role is Role.PRODUCT_OWNER)
    assert po.label == "Tara"
    others = [a for a in agents if a.role is not Role.PRODUCT_OWNER]
    for a in others:
        assert a.label == a.name  # unnamed, fall back to stable name


def test_shared_agents_default():
    portfolio = PortfolioConfig(owner="x@y.com")
    agents = build_shared_agents(portfolio, SHARED_EXECUTIVE)
    roles = [a.role for a in agents]
    assert Role.CEO in roles
    assert Role.CTO in roles
    assert all(a.display_name is None for a in agents)


def test_shared_named_agents_with_multi_seat_org_owner():
    portfolio = PortfolioConfig(
        owner="x@y.com",
        named_agents={
            "ceo": "Cassia",
            "cto": "Theo",
            "org_owner": ["Otto", "Mae"],
        },
    )
    agents = build_shared_agents(portfolio, SHARED_EXECUTIVE)
    ceo = next(a for a in agents if a.role is Role.CEO)
    assert ceo.display_name == "Cassia"
    org_owners = [a for a in agents if a.role is Role.ORG_OWNER]
    assert len(org_owners) == 2
    assert [a.display_name for a in org_owners] == ["Otto", "Mae"]
    assert org_owners[0].seat_index == 0
    assert org_owners[1].seat_index == 1
    assert org_owners[1].name == "org_owner@org#1"


def test_audit_layer_default_one_seat_per_role():
    portfolio = PortfolioConfig(owner="x@y.com")
    agents = build_shared_agents(portfolio, AUDIT)
    assert len(agents) == 5
    assert {a.role for a in agents} == set(AUDIT)


def test_build_named_agent_for_project(tmp_path):
    manifest = _write_manifest(tmp_path, {"product_owner": "Tara"})
    agent = build_named_agent(Role.PRODUCT_OWNER, project="Demo", manifest=manifest)
    assert agent.display_name == "Tara"


def test_build_named_agent_shared():
    portfolio = PortfolioConfig(owner="x@y.com", named_agents={"ceo": "Cassia"})
    agent = build_named_agent(Role.CEO, project=None, portfolio=portfolio)
    assert agent.display_name == "Cassia"


def test_project_role_slots_respects_team_overrides():
    manifest = load_manifest(REPO_ROOT / "projects" / "demo_two.yaml")
    # demo_two disables security_champion AND has engineers=1
    roles = project_role_slots(manifest)
    assert Role.SECURITY_CHAMPION not in roles
    assert sum(1 for r in roles if r is Role.ENGINEER) == 1
    assert Role.INTERN not in roles  # intern disabled in v0 manifests
