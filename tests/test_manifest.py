from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from minions.models.manifest import (
    DossierConfig,
    Manifest,
    load_active_manifests,
    load_manifest,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skip(reason="fixture-coupled to private project YAMLs; smoke-tested by operator")
def test_load_demo_manifest():
    m = load_manifest(REPO_ROOT / "projects" / "demo.yaml")
    assert m.name == "Demo"
    assert m.source.kind == "github"
    assert m.source.repo == "your-github-org/demo"
    assert m.weekly_budget_usd == 1.00
    assert m.monthly_budget_usd == 4.00
    assert m.cadence_profile == "v0_frugal"
    assert m.delivery_targets.scope == "portfolio"
    assert m.delivery_targets.share_weight == 0.3


@pytest.mark.skip(reason="fixture-coupled to private project YAMLs; smoke-tested by operator")
def test_priority_ordering_top_three():
    """demo_five > demo_four > demo_two > {demo, demo_three} per operator priority."""
    manifests = load_active_manifests(REPO_ROOT / "projects")
    weights = {name: m.delivery_targets.share_weight for name, m in manifests.items()}
    assert weights["demo_five"] > weights["demo_four"]
    assert weights["demo_four"] > weights["demo_two"]
    assert weights["demo_two"] > weights["Demo"]
    assert weights["demo_two"] > weights["demo_three"]


@pytest.mark.skip(reason="fixture-coupled to private project YAMLs; smoke-tested by operator")
def test_load_active_manifests_skips_deferred():
    manifests = load_active_manifests(REPO_ROOT / "projects")
    assert "trading" not in manifests
    assert set(manifests.keys()) == {"Demo", "demo_two", "demo_three", "demo_four", "demo_five"}


def test_total_active_budget_within_envelope():
    manifests = load_active_manifests(REPO_ROOT / "projects")
    total = sum(m.monthly_budget_usd for m in manifests.values())
    assert 14.0 <= total <= 30.0


def test_dossier_config_defaults_when_absent():
    """A manifest without a `dossier:` block gets a default DossierConfig."""
    m = load_manifest(REPO_ROOT / "projects" / "demo.yaml")
    assert isinstance(m.dossier, DossierConfig)
    assert m.dossier.publish is True
    assert m.dossier.max_new_issues_per_cycle == 5
    assert m.dossier.freshness_overrides.ok_max_age_days == 14
    assert m.dossier.freshness_overrides.ok_max_commit_drift == 200


def test_dossier_config_overrides_parse(tmp_path: Path):
    """A manifest with explicit dossier overrides loads them."""
    src = REPO_ROOT / "projects" / "demo.yaml"
    data = yaml.safe_load(src.read_text())
    data["dossier"] = {
        "publish": False,
        "max_new_issues_per_cycle": 2,
        "freshness_overrides": {
            "ok_max_age_days": 7,
            "ok_max_commit_drift": 50,
        },
    }
    out = tmp_path / "demo.yaml"
    out.write_text(yaml.safe_dump(data))

    m = load_manifest(out)
    assert m.dossier.publish is False
    assert m.dossier.max_new_issues_per_cycle == 2
    assert m.dossier.freshness_overrides.ok_max_age_days == 7
    assert m.dossier.freshness_overrides.ok_max_commit_drift == 50


def test_dossier_rejects_negative_issue_cap(tmp_path: Path):
    """Negative max_new_issues_per_cycle is a hard failure."""
    src = REPO_ROOT / "projects" / "demo.yaml"
    data = yaml.safe_load(src.read_text())
    data["dossier"] = {"max_new_issues_per_cycle": -1}
    out = tmp_path / "demo.yaml"
    out.write_text(yaml.safe_dump(data))

    with pytest.raises(ValueError, match="max_new_issues_per_cycle"):
        load_manifest(out)


def test_dossier_config_directly_rejects_negative():
    with pytest.raises(ValidationError):
        DossierConfig(max_new_issues_per_cycle=-1)


def test_active_manifests_all_have_dossier_defaults():
    """Every shipped manifest must satisfy the schema, dossier included."""
    manifests = load_active_manifests(REPO_ROOT / "projects")
    for m in manifests.values():
        assert isinstance(m, Manifest)
        assert isinstance(m.dossier, DossierConfig)
        assert m.dossier.max_new_issues_per_cycle >= 0
