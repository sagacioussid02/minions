"""Tests for src/minions/cost.py."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from minions.cost import (
    PRICING,
    CostEntry,
    _litellm_cost_callback,
    append_entry,
    clear_attribution,
    cost_by_project,
    estimate_cost_usd,
    get_attribution,
    init_cost_tracking,
    month_to_date_cost,
    read_log,
    resolve_tier,
    set_attribution,
    set_log_path,
    week_to_date_cost,
)


@pytest.fixture(autouse=True)
def _isolated_log_path(tmp_path: Path) -> Path:
    """Each test gets its own cost log."""
    p = tmp_path / "cost_log.jsonl"
    set_log_path(p)
    clear_attribution()
    yield p
    clear_attribution()


# ---- pricing ----------------------------------------------------------------


def test_resolve_tier_handles_litellm_style_ids() -> None:
    assert resolve_tier("anthropic/claude-haiku-4-5") == "haiku"
    assert resolve_tier("claude-sonnet-4-6") == "sonnet"
    assert resolve_tier("opus-4.7") == "opus"
    assert resolve_tier("Claude_Sonnet_4_6") == "sonnet"


def test_resolve_tier_unknown_returns_none() -> None:
    assert resolve_tier("gpt-4o") is None
    assert resolve_tier("") is None


def test_estimate_cost_haiku() -> None:
    # 1M input + 1M output haiku = $1 + $5 = $6
    assert estimate_cost_usd("claude-haiku-4-5", 1_000_000, 1_000_000) == pytest.approx(6.0)


def test_estimate_cost_sonnet_partial() -> None:
    # 100k input + 50k output sonnet = 0.30 + 0.75 = 1.05
    cost = estimate_cost_usd("claude-sonnet-4-6", 100_000, 50_000)
    assert cost == pytest.approx(1.05)


def test_estimate_cost_unknown_model_zero() -> None:
    assert estimate_cost_usd("gpt-4o", 1_000_000, 1_000_000) == 0.0


def test_pricing_table_has_all_three_tiers() -> None:
    assert {"haiku", "sonnet", "opus"} <= set(PRICING.keys())


# ---- attribution context ----------------------------------------------------


def test_attribution_default_blank() -> None:
    a = get_attribution()
    assert a == {"project": "", "decision_id": "", "role": ""}


def test_set_and_get_attribution() -> None:
    set_attribution(project="demo_three", decision_id="abc-123", role="manager")
    a = get_attribution()
    assert a["project"] == "demo_three"
    assert a["decision_id"] == "abc-123"
    assert a["role"] == "manager"


def test_clear_attribution_resets() -> None:
    set_attribution(project="x", role="y")
    clear_attribution()
    assert get_attribution()["project"] == ""


# ---- log read/write ---------------------------------------------------------


def _entry(**kwargs):
    base = {
        "timestamp": datetime.now(tz=UTC),
        "project": "p",
        "decision_id": "",
        "role": "r",
        "model": "claude-sonnet-4-6",
        "input_tokens": 10,
        "output_tokens": 5,
        "cost_usd": 0.001,
    }
    base.update(kwargs)
    return CostEntry(**base)


def test_append_and_read_round_trip(_isolated_log_path: Path) -> None:
    e1 = _entry(project="a", cost_usd=0.10)
    e2 = _entry(project="b", cost_usd=0.20)
    append_entry(e1)
    append_entry(e2)
    out = read_log()
    assert len(out) == 2
    assert {x.project for x in out} == {"a", "b"}


def test_read_log_skips_malformed_lines(_isolated_log_path: Path) -> None:
    p = _isolated_log_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"\n{{not json}}\n{json.dumps(_entry(project='good').to_dict())}\n")
    out = read_log()
    assert len(out) == 1
    assert out[0].project == "good"


# ---- aggregation ------------------------------------------------------------


def test_cost_by_project_sums(_isolated_log_path: Path) -> None:
    append_entry(_entry(project="a", cost_usd=0.10))
    append_entry(_entry(project="a", cost_usd=0.05))
    append_entry(_entry(project="b", cost_usd=0.20))
    totals = cost_by_project()
    assert totals == pytest.approx({"a": 0.15, "b": 0.20})


def test_cost_by_project_filters_by_since(_isolated_log_path: Path) -> None:
    now = datetime.now(tz=UTC)
    append_entry(_entry(project="a", cost_usd=0.10, timestamp=now - timedelta(days=10)))
    append_entry(_entry(project="a", cost_usd=0.05, timestamp=now - timedelta(hours=1)))
    cutoff = now - timedelta(days=1)
    totals = cost_by_project(since=cutoff)
    assert totals == pytest.approx({"a": 0.05})


def test_month_to_date_includes_only_current_month(_isolated_log_path: Path) -> None:
    now = datetime(2026, 5, 15, 12, 0, tzinfo=UTC)
    append_entry(_entry(project="x", cost_usd=1.00, timestamp=datetime(2026, 4, 30, tzinfo=UTC)))
    append_entry(_entry(project="x", cost_usd=2.00, timestamp=datetime(2026, 5, 1, tzinfo=UTC)))
    append_entry(_entry(project="x", cost_usd=3.00, timestamp=datetime(2026, 5, 15, tzinfo=UTC)))
    assert month_to_date_cost("x", now=now) == pytest.approx(5.0)


def test_week_to_date_starts_on_monday(_isolated_log_path: Path) -> None:
    # Wed 2026-05-13
    now = datetime(2026, 5, 13, 12, 0, tzinfo=UTC)
    append_entry(
        _entry(project="x", cost_usd=1.00, timestamp=datetime(2026, 5, 10, tzinfo=UTC))
    )  # Sun before
    append_entry(
        _entry(project="x", cost_usd=2.00, timestamp=datetime(2026, 5, 11, tzinfo=UTC))
    )  # Mon — included
    append_entry(_entry(project="x", cost_usd=3.00, timestamp=datetime(2026, 5, 13, tzinfo=UTC)))
    assert week_to_date_cost("x", now=now) == pytest.approx(5.0)


def test_empty_log_returns_zero(_isolated_log_path: Path) -> None:
    assert month_to_date_cost("anything") == 0.0
    assert read_log() == []


# ---- LiteLLM callback (synthetic) ------------------------------------------


def test_litellm_callback_records_with_attribution(_isolated_log_path: Path) -> None:
    set_attribution(project="demo_three", decision_id="dec-1", role="manager")
    fake_response = SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=500)
    )
    _litellm_cost_callback(
        kwargs={"model": "claude-sonnet-4-6"},
        completion_response=fake_response,
        start_time=None,
        end_time=None,
    )
    out = read_log()
    assert len(out) == 1
    assert out[0].project == "demo_three"
    assert out[0].decision_id == "dec-1"
    assert out[0].input_tokens == 1000
    # 1000 input * $3/M + 500 output * $15/M = 0.003 + 0.0075 = 0.0105
    assert out[0].cost_usd == pytest.approx(0.0105)


def test_litellm_callback_swallows_exceptions(_isolated_log_path: Path) -> None:
    """Bad inputs must not raise — observability never crashes work."""
    _litellm_cost_callback(kwargs=None, completion_response=None, start_time=None, end_time=None)  # type: ignore[arg-type]
    assert read_log() == []


def test_litellm_callback_no_usage_skips(_isolated_log_path: Path) -> None:
    fake_response = SimpleNamespace()  # no .usage
    _litellm_cost_callback(
        kwargs={"model": "claude-sonnet-4-6"},
        completion_response=fake_response,
        start_time=None,
        end_time=None,
    )
    assert read_log() == []


def test_init_cost_tracking_idempotent(
    monkeypatch: pytest.MonkeyPatch, _isolated_log_path: Path
) -> None:
    """Registering twice should not create duplicate callbacks."""
    fake_litellm = SimpleNamespace(success_callback=[])
    monkeypatch.setitem(__import__("sys").modules, "litellm", fake_litellm)

    init_cost_tracking()
    init_cost_tracking()
    init_cost_tracking()
    assert fake_litellm.success_callback.count(_litellm_cost_callback) == 1
