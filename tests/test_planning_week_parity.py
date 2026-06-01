"""Biweekly rotation gate — exercises ``_week_parity`` + the Manifest field.

The gate lives inside ``run_weekly_planning`` and is straightforward to
reason about once the pure helper + the field validation are pinned
down. The Manifest tests here also guard against silent type changes
on ``planning_week_parity``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from minions.models.manifest import Manifest, ManifestSource
from minions.scheduled.weekly_planning import _week_parity


def _m(**kw) -> Manifest:
    return Manifest(
        name="x",
        description="x",
        source=ManifestSource(kind="github", repo="owner/x"),
        weekly_budget_usd=1.0,
        monthly_budget_usd=4.0,
        owner="op@example.com",
        **kw,
    )


def test_week_parity_returns_odd_for_iso_week_3() -> None:
    # 2026-01-12 (Mon) = ISO week 3.
    assert _week_parity(datetime(2026, 1, 12, tzinfo=UTC)) == "odd"


def test_week_parity_returns_even_for_iso_week_4() -> None:
    # 2026-01-19 (Mon) = ISO week 4.
    assert _week_parity(datetime(2026, 1, 19, tzinfo=UTC)) == "even"


def test_week_parity_handles_year_boundary() -> None:
    # ISO week 1 of 2026 starts on 2025-12-29 (Mon) — assert the helper
    # still produces a valid parity value across the boundary.
    parity = _week_parity(datetime(2025, 12, 29, tzinfo=UTC))
    assert parity in ("odd", "even")


def test_manifest_planning_week_parity_defaults_to_any() -> None:
    assert _m().planning_week_parity == "any"


def test_manifest_planning_week_parity_accepts_odd_and_even() -> None:
    assert _m(planning_week_parity="odd").planning_week_parity == "odd"
    assert _m(planning_week_parity="even").planning_week_parity == "even"


def test_manifest_planning_week_parity_rejects_invalid_value() -> None:
    with pytest.raises(Exception):  # pydantic ValidationError
        _m(planning_week_parity="alternate")  # type: ignore[arg-type]
