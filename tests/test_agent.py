from pathlib import Path

from minions.agents.base import MinionAgent
from minions.models.manifest import load_manifest
from minions.models.roles import ModelTier, Role

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_safety_preamble_present_in_system_prompt():
    agent = MinionAgent.for_role(Role.ENGINEER, project="Demo", cadence="v0_frugal")
    sp = agent.system_prompt
    assert "MUST NOT read .env" in sp
    assert "MUST NOT push commits to the `main`" in sp
    assert "Decision Record" in sp
    assert "Terms of Service" in sp


def test_role_name_includes_project():
    agent = MinionAgent.for_role(Role.PRODUCT_OWNER, project="demo_two", cadence="v0_frugal")
    assert agent.name == "product_owner@demo_two"


def test_shared_role_has_org_scope():
    agent = MinionAgent.for_role(Role.CHIEF_AUDITOR, project=None, cadence="v0_frugal")
    assert agent.name == "chief_auditor@org"


def test_engineer_is_haiku_in_v0():
    agent = MinionAgent.for_role(Role.ENGINEER, project="Demo", cadence="v0_frugal")
    assert agent.tier is ModelTier.HAIKU


def test_principal_is_sonnet_in_v0():
    agent = MinionAgent.for_role(Role.PRINCIPAL, project="Demo", cadence="v0_frugal")
    assert agent.tier is ModelTier.SONNET


def test_manifest_tier_override_applies():
    manifest = load_manifest(REPO_ROOT / "projects" / "Demo.yaml")
    agent = MinionAgent.for_role(
        Role.PRINCIPAL, project="Demo", cadence=manifest.cadence_profile, manifest=manifest
    )
    # Demo.yaml sets `principal: sonnet` explicitly — same as v0 default but exercises the path.
    assert agent.tier is ModelTier.SONNET
