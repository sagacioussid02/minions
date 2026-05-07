from pathlib import Path

from minions.models.manifest import load_active_manifests, load_manifest

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_load_demo_manifest():
    m = load_manifest(REPO_ROOT / "projects" / "demo.yaml")
    assert m.name == "Demo"
    assert m.source.kind == "github"
    assert m.source.repo == "your-org/demo-app"
    assert m.weekly_budget_usd == 1.00
    assert m.monthly_budget_usd == 4.00
    assert m.cadence_profile == "v0_frugal"
    assert m.delivery_targets.scope == "portfolio"
    assert m.delivery_targets.share_weight == 0.3


def test_priority_ordering_top_three():
    """demo_three > demo_two > demo_four > {demo, demo_five} per operator priority."""
    manifests = load_active_manifests(REPO_ROOT / "projects")
    weights = {name: m.delivery_targets.share_weight for name, m in manifests.items()}
    assert weights["demo_three"] > weights["demo_two"]
    assert weights["demo_two"] > weights["demo_four"]
    assert weights["demo_four"] > weights["Demo"]
    assert weights["demo_four"] > weights["demo_five"]


def test_load_active_manifests_skips_deferred():
    manifests = load_active_manifests(REPO_ROOT / "projects")
    assert "trading" not in manifests
    assert set(manifests.keys()) == {"Demo", "demo_four", "demo_five", "demo_two", "demo_three"}


def test_total_active_budget_within_envelope():
    manifests = load_active_manifests(REPO_ROOT / "projects")
    total = sum(m.monthly_budget_usd for m in manifests.values())
    assert 14.0 <= total <= 30.0
