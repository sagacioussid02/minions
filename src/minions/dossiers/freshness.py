"""Dossier freshness — age + commit-drift labels read by planning.

Pure functions. No store access here; callers fetch the latest merged draft
themselves and ask this module for a label.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from minions.models.dossier import DossierDraft
from minions.models.manifest import DossierFreshnessOverrides

FreshnessLabel = Literal["ok", "stale", "very_stale", "none"]


@dataclass(frozen=True)
class FreshnessReport:
    label: FreshnessLabel
    age_days: int | None
    commit_drift: int | None
    reason: str


def compute_freshness(
    draft: DossierDraft | None,
    *,
    overrides: DossierFreshnessOverrides,
    repo_root: Path | None = None,
    now: datetime | None = None,
) -> FreshnessReport:
    """Compute a freshness label for the latest merged dossier draft.

    Returns ``none`` when no draft exists; callers should treat ``none`` as
    "ungrounded" — planning will still run, but with a flag.
    """
    if draft is None:
        return FreshnessReport(
            label="none",
            age_days=None,
            commit_drift=None,
            reason="no merged dossier found",
        )

    now_dt = now or datetime.now(UTC)
    age_days = max(0, (now_dt - draft.generated_at).days)
    drift = _commit_drift(repo_root, draft.commit_sha) if repo_root else None

    age_ok = age_days <= overrides.ok_max_age_days
    drift_ok = drift is None or drift <= overrides.ok_max_commit_drift
    if age_ok and drift_ok:
        return FreshnessReport(
            label="ok",
            age_days=age_days,
            commit_drift=drift,
            reason=f"age={age_days}d, drift={drift}",
        )

    age_stale = age_days <= overrides.stale_max_age_days
    drift_stale = drift is None or drift <= overrides.stale_max_commit_drift
    if age_stale and drift_stale:
        return FreshnessReport(
            label="stale",
            age_days=age_days,
            commit_drift=drift,
            reason=f"age={age_days}d > {overrides.ok_max_age_days}d "
            f"or drift={drift} > {overrides.ok_max_commit_drift}",
        )

    return FreshnessReport(
        label="very_stale",
        age_days=age_days,
        commit_drift=drift,
        reason=f"age={age_days}d, drift={drift} — past stale thresholds "
        f"({overrides.stale_max_age_days}d / {overrides.stale_max_commit_drift})",
    )


def _commit_drift(repo_root: Path, sha: str) -> int | None:
    """Count commits between ``sha`` and HEAD. None if git not available."""
    if not repo_root or not (repo_root / ".git").exists() or sha == "unknown":
        return None
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_root), "rev-list", "--count", f"{sha}..HEAD"],
            check=True, capture_output=True, text=True, timeout=5,
        )
        return int(out.stdout.strip() or 0)
    except (subprocess.SubprocessError, OSError, ValueError):
        return None
