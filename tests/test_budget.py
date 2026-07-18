"""Tests for src/minions/budget.py."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from minions.budget import (
    BREACH_THRESHOLD,
    WARN_THRESHOLD,
    BudgetBreachError,
    BudgetState,
    assert_can_run_engineer,
    evaluate,
    maybe_notify,
)
from minions.cost import CostEntry, append_entry, set_log_path
from minions.models.manifest import Manifest


def _make_manifest(name: str, monthly_budget: float, tmp_path: Path) -> Manifest:
    return Manifest.model_validate(
        {
            "name": name,
            "description": "test",
            "source": {"kind": "local", "path": str(tmp_path), "default_branch": "main"},
            "weekly_budget_usd": monthly_budget / 4,
            "monthly_budget_usd": monthly_budget,
            "owner": "owner@example.com",
        }
    )


def _seed_cost(project: str, cost_usd: float, *, when: datetime | None = None) -> None:
    when = when or datetime.now(tz=UTC)
    append_entry(
        CostEntry(
            timestamp=when,
            project=project,
            decision_id="",
            role="planning",
            model="claude-sonnet-4-6",
            input_tokens=0,
            output_tokens=0,
            cost_usd=cost_usd,
        )
    )


@pytest.fixture(autouse=True)
def _isolated_log(tmp_path: Path) -> Path:
    p = tmp_path / "cost_log.jsonl"
    set_log_path(p)
    return p


# ---- evaluate --------------------------------------------------------------


def test_evaluate_ok_when_under_warn(tmp_path: Path) -> None:
    m = _make_manifest("p", 4.0, tmp_path)
    _seed_cost("p", 1.0)  # 25%
    state = evaluate(m)
    assert state.state == "ok"
    assert not state.is_throttled
    assert not state.is_breached
    assert state.fraction == pytest.approx(0.25)


def test_evaluate_warn_at_threshold(tmp_path: Path) -> None:
    m = _make_manifest("p", 10.0, tmp_path)
    _seed_cost("p", 8.0)  # 80% exactly
    state = evaluate(m)
    assert state.state == "warn"
    assert state.is_throttled
    assert not state.is_breached


def test_evaluate_breach_at_or_above_full_cap(tmp_path: Path) -> None:
    m = _make_manifest("p", 4.0, tmp_path)
    _seed_cost("p", 4.0)
    state = evaluate(m)
    assert state.state == "breach"
    assert state.is_throttled
    assert state.is_breached


def test_evaluate_zero_cap_does_not_divide(tmp_path: Path) -> None:
    m = _make_manifest("p", 0.0, tmp_path)
    state = evaluate(m)
    assert state.fraction == 0.0
    assert state.state == "ok"


def test_thresholds_are_what_we_documented() -> None:
    assert WARN_THRESHOLD == 0.80
    assert BREACH_THRESHOLD == 1.00


def test_evaluate_only_counts_current_month(tmp_path: Path) -> None:
    m = _make_manifest("p", 10.0, tmp_path)
    last_month = datetime.now(tz=UTC).replace(day=1) - timedelta(days=5)
    _seed_cost("p", 9.0, when=last_month)
    _seed_cost("p", 1.0)
    state = evaluate(m)
    assert state.month_to_date_usd == pytest.approx(1.0)


# ---- sandbox lifetime cap ----------------------------------------------


def _make_sandbox_manifest(
    name: str, monthly_budget: float, sandbox_budget: float, tmp_path: Path
) -> Manifest:
    return Manifest.model_validate(
        {
            "name": name,
            "description": "test",
            "source": {"kind": "local", "path": str(tmp_path), "default_branch": "main"},
            "weekly_budget_usd": monthly_budget / 4,
            "monthly_budget_usd": monthly_budget,
            "owner": "owner@example.com",
            "sandbox_budget_usd": sandbox_budget,
        }
    )


def test_sandbox_cap_breaches_even_though_monthly_fraction_is_fine(tmp_path: Path) -> None:
    """A huge monthly cap alone wouldn't throttle; the small lifetime cap does."""
    m = _make_sandbox_manifest("sandbox", monthly_budget=1000.0, sandbox_budget=2.0, tmp_path=tmp_path)
    _seed_cost("sandbox", 2.0)  # 0.2% of monthly cap, but == 100% of sandbox cap
    state = evaluate(m)
    assert state.state == "breach"
    assert state.is_breached


def test_sandbox_cap_is_lifetime_not_monthly(tmp_path: Path) -> None:
    """Spend from a prior month still counts against the sandbox cap, unlike
    the monthly cap which resets — a sandbox can't be re-farmed by waiting."""
    m = _make_sandbox_manifest("sandbox", monthly_budget=10.0, sandbox_budget=2.0, tmp_path=tmp_path)
    last_month = datetime.now(tz=UTC).replace(day=1) - timedelta(days=5)
    _seed_cost("sandbox", 1.5, when=last_month)
    _seed_cost("sandbox", 1.0)  # this month alone: 10% of monthly cap — fine
    state = evaluate(m)
    # lifetime: 2.5 / 2.0 = 125% -> breach, even though month-to-date is only 10%
    assert state.month_to_date_usd == pytest.approx(1.0)
    assert state.state == "breach"


def test_no_sandbox_cap_means_only_monthly_cap_applies(tmp_path: Path) -> None:
    """Regression guard: manifests without sandbox_budget_usd are unaffected."""
    m = _make_manifest("p", 10.0, tmp_path)
    assert m.sandbox_budget_usd is None
    _seed_cost("p", 1.0)
    state = evaluate(m)
    assert state.state == "ok"
    assert state.fraction == pytest.approx(0.1)


def test_evaluate_isolates_by_project(tmp_path: Path) -> None:
    m_a = _make_manifest("a", 4.0, tmp_path)
    m_b = _make_manifest("b", 4.0, tmp_path)
    _seed_cost("a", 5.0)  # b not affected
    assert evaluate(m_a).is_breached
    assert evaluate(m_b).state == "ok"


# ---- assert_can_run_engineer -----------------------------------------------


def test_assert_can_run_engineer_passes_when_ok(tmp_path: Path) -> None:
    state = BudgetState(
        project="p", monthly_cap_usd=4.0, month_to_date_usd=0.5, fraction=0.125, state="ok"
    )
    assert_can_run_engineer(state)  # no raise


def test_assert_can_run_engineer_passes_at_warn(tmp_path: Path) -> None:
    state = BudgetState(
        project="p", monthly_cap_usd=4.0, month_to_date_usd=3.5, fraction=0.875, state="warn"
    )
    assert_can_run_engineer(state)  # warn is still allowed


def test_assert_can_run_engineer_raises_at_breach(tmp_path: Path) -> None:
    state = BudgetState(
        project="p", monthly_cap_usd=4.0, month_to_date_usd=4.5, fraction=1.125, state="breach"
    )
    with pytest.raises(BudgetBreachError):
        assert_can_run_engineer(state)


# ---- notification de-dup ---------------------------------------------------


class _Recorder:
    def __init__(self) -> None:
        self.texts: list[tuple[str, str]] = []

    def notify_approval_request(self, decision):  # type: ignore[no-untyped-def]
        pass

    def notify_decision_resolved(self, decision):  # type: ignore[no-untyped-def]
        pass

    def notify_text(self, *, subject: str, body: str) -> None:
        self.texts.append((subject, body))


def test_maybe_notify_sends_once_per_state_per_month(tmp_path: Path) -> None:
    n = _Recorder()
    nfile = tmp_path / "notifs.json"
    state = BudgetState(
        project="p", monthly_cap_usd=4.0, month_to_date_usd=3.5, fraction=0.875, state="warn"
    )
    assert maybe_notify(state, notifier=n, notifications_path=nfile) is True
    assert maybe_notify(state, notifier=n, notifications_path=nfile) is False
    assert len(n.texts) == 1
    assert "WARN" in n.texts[0][0]


def test_maybe_notify_distinguishes_warn_and_breach(tmp_path: Path) -> None:
    n = _Recorder()
    nfile = tmp_path / "notifs.json"
    warn_state = BudgetState(
        project="p", monthly_cap_usd=4.0, month_to_date_usd=3.5, fraction=0.875, state="warn"
    )
    breach_state = BudgetState(
        project="p", monthly_cap_usd=4.0, month_to_date_usd=5.0, fraction=1.25, state="breach"
    )
    assert maybe_notify(warn_state, notifier=n, notifications_path=nfile) is True
    # Same project, escalated to breach — should fire again.
    assert maybe_notify(breach_state, notifier=n, notifications_path=nfile) is True
    assert len(n.texts) == 2


def test_maybe_notify_skips_ok(tmp_path: Path) -> None:
    n = _Recorder()
    nfile = tmp_path / "notifs.json"
    state = BudgetState(
        project="p", monthly_cap_usd=4.0, month_to_date_usd=0.0, fraction=0.0, state="ok"
    )
    assert maybe_notify(state, notifier=n, notifications_path=nfile) is False
    assert n.texts == []


def test_maybe_notify_persists_across_calls(tmp_path: Path) -> None:
    nfile = tmp_path / "notifs.json"
    state = BudgetState(
        project="p", monthly_cap_usd=4.0, month_to_date_usd=3.5, fraction=0.875, state="warn"
    )
    maybe_notify(state, notifier=_Recorder(), notifications_path=nfile)
    data = json.loads(nfile.read_text())
    month = datetime.now(tz=UTC).strftime("%Y-%m")
    assert "p:warn" in data[month]
