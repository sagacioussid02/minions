"""Tests for §9.3 — Devil's Advocate pre-approval hook.

Focus on the gate logic + attach_critique() wrapper. The underlying critique()
LLM-call path is exercised by tests/test_planning_crew.py-style override mocks.
"""

from __future__ import annotations

from pathlib import Path

from minions.activity import read_log as read_activity_log
from minions.activity import set_log_path as set_activity_path
from minions.cost import set_log_path as set_cost_path
from minions.crews.devils_advocate import (
    _TRIGGER_RISKS,
    attach_critique,
    should_critique,
)
from minions.models.decision import Decision, DecisionType, DevilsAdvocateCritique


def _decision(risk: str) -> Decision:
    return Decision(
        project="demo_five",
        type=DecisionType.FEATURE,
        summary="risky thing",
        rationale="r",
        diff_or_plan="p",
        risk=risk,
        proposer_role="manager",
        proposer_agent_id="m@p",
    )


def _override() -> DevilsAdvocateCritique:
    return DevilsAdvocateCritique(
        counter_argument="this won't scale past 100k requests",
        failure_modes=["DB lock contention", "thundering herd on cache miss"],
        alternative_considered="add a queue + worker pool first",
    )


# ---- gate logic ------------------------------------------------------------


def test_gate_fires_on_medium() -> None:
    assert should_critique(_decision("medium")) is True


def test_gate_fires_on_high() -> None:
    assert should_critique(_decision("high")) is True


def test_gate_skips_low() -> None:
    assert should_critique(_decision("low")) is False


def test_trigger_risks_documented() -> None:
    assert frozenset({"medium", "high"}) == _TRIGGER_RISKS


# ---- attach_critique behavior ---------------------------------------------


def test_attach_critique_low_risk_is_noop(tmp_path: Path) -> None:
    set_cost_path(tmp_path / "cost.jsonl")
    set_activity_path(tmp_path / "activity.jsonl")
    d = _decision("low")
    result = attach_critique(d, output_override=_override())  # override would normally win
    assert result is None  # gate blocks before override is consulted
    assert d.critique is None
    # No crew_run should have fired.
    assert read_activity_log() == []


def test_attach_critique_uses_override_when_provided(tmp_path: Path) -> None:
    set_cost_path(tmp_path / "cost.jsonl")
    set_activity_path(tmp_path / "activity.jsonl")
    d = _decision("medium")
    result = attach_critique(d, output_override=_override())
    assert result is not None
    assert result.counter_argument.startswith("this won't")
    assert d.critique is not None
    assert d.critique.failure_modes == [
        "DB lock contention",
        "thundering herd on cache miss",
    ]


def test_attach_critique_brackets_with_activity_log(tmp_path: Path) -> None:
    set_cost_path(tmp_path / "cost.jsonl")
    set_activity_path(tmp_path / "activity.jsonl")
    attach_critique(_decision("high"), output_override=_override())

    events = read_activity_log()
    # The critique is now also captured as a transcript turn (agent_spoke)
    # so it surfaces in the meetings feed.
    assert [e.event for e in events] == ["crew_started", "agent_spoke", "crew_finished"]
    assert events[0].crew == "devils_advocate"
    assert "devils_advocate" in events[0].agents


def test_attach_critique_no_api_key_no_override_skips(tmp_path: Path) -> None:
    """Real run with no api_key returns None without raising."""
    set_cost_path(tmp_path / "cost.jsonl")
    set_activity_path(tmp_path / "activity.jsonl")
    d = _decision("medium")
    result = attach_critique(d, api_key=None, output_override=None)
    assert result is None
    assert d.critique is None


def test_attach_critique_clears_attribution_on_failure(tmp_path: Path) -> None:
    """If critique() raises, the cost-attribution contextvar must still be cleared."""
    from minions import cost as cost_module
    from minions.crews import devils_advocate as da

    set_cost_path(tmp_path / "cost.jsonl")
    set_activity_path(tmp_path / "activity.jsonl")

    # Patch critique() to raise. Use a sentinel to verify cleanup happened.
    real_critique = da.critique

    def boom(*args, **kwargs):
        raise RuntimeError("forced failure")

    da.critique = boom
    try:
        try:
            attach_critique(_decision("medium"), output_override=None, api_key="x")
        except RuntimeError:
            pass
    finally:
        da.critique = real_critique

    # Attribution should be cleared.
    assert cost_module.get_attribution()["role"] == ""
