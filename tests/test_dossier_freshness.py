"""Tests for src/minions/dossiers/freshness.py."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from minions.dossiers.freshness import compute_freshness
from minions.models.dossier import DossierDraft, DossierStatus
from minions.models.manifest import DossierFreshnessOverrides


def _draft(age_days: int) -> DossierDraft:
    return DossierDraft(
        project="p",
        commit_sha="abc",
        status=DossierStatus.MERGED,
        markdown="# x",
        generated_at=datetime.now(UTC) - timedelta(days=age_days),
    )


def test_none_when_no_draft() -> None:
    f = compute_freshness(None, overrides=DossierFreshnessOverrides())
    assert f.label == "none"
    assert "no merged dossier" in f.reason


def test_ok_when_recent() -> None:
    f = compute_freshness(_draft(3), overrides=DossierFreshnessOverrides())
    assert f.label == "ok"
    assert f.age_days == 3


def test_stale_at_boundary() -> None:
    # Default ok=14d, stale=30d.
    f = compute_freshness(_draft(20), overrides=DossierFreshnessOverrides())
    assert f.label == "stale"


def test_very_stale_past_30d() -> None:
    f = compute_freshness(_draft(45), overrides=DossierFreshnessOverrides())
    assert f.label == "very_stale"


def test_custom_overrides_honored() -> None:
    tight = DossierFreshnessOverrides(
        ok_max_age_days=2, stale_max_age_days=5,
        ok_max_commit_drift=10, stale_max_commit_drift=20,
    )
    f = compute_freshness(_draft(3), overrides=tight)
    assert f.label == "stale"
    f2 = compute_freshness(_draft(10), overrides=tight)
    assert f2.label == "very_stale"
