"""Tests for src/minions/activity.py."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from minions import activity
from minions.activity import (
    RUNNING_WINDOW_SECONDS,
    ActivityEntry,
    append,
    crew_run,
    history_for_role,
    is_role_running,
    read_log,
    running_now,
    set_log_path,
)


@pytest.fixture(autouse=True)
def _isolated_log(tmp_path: Path) -> Path:
    p = tmp_path / "activity.jsonl"
    set_log_path(p)
    yield p
    activity._log_path_override = None


def _entry(**kwargs):
    base = {
        "timestamp": datetime.now(tz=UTC),
        "event": "crew_started",
        "run_id": "r1",
        "crew": "planning",
        "project": "p",
        "decision_id": "",
        "agents": ("manager",),
    }
    base.update(kwargs)
    return ActivityEntry(**base)


# ---- read/write -------------------------------------------------------------


def test_append_and_read_round_trip() -> None:
    e = _entry(crew="engineer", agents=("engineer", "tech_team_lead"))
    append(e)
    out = read_log()
    assert len(out) == 1
    assert out[0].crew == "engineer"
    assert out[0].agents == ("engineer", "tech_team_lead")


def test_read_log_skips_malformed() -> None:
    p = activity.get_log_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("not json\n" + str({"event": "crew_started"}) + "\n")
    assert read_log() == []


# ---- running_now ------------------------------------------------------------


def test_running_now_includes_unclosed_starts() -> None:
    append(_entry(run_id="r1"))
    assert len(running_now()) == 1


def test_running_now_excludes_finished_runs() -> None:
    now = datetime.now(tz=UTC)
    append(_entry(run_id="r1", timestamp=now - timedelta(seconds=10)))
    append(_entry(run_id="r1", event="crew_finished", timestamp=now))
    assert running_now() == []


def test_running_now_excludes_failed_runs() -> None:
    now = datetime.now(tz=UTC)
    append(_entry(run_id="r1", timestamp=now - timedelta(seconds=10)))
    append(_entry(run_id="r1", event="crew_failed", timestamp=now))
    assert running_now() == []


def test_running_now_skips_expired_starts() -> None:
    """Stale starts (older than the window) shouldn't paint the dashboard."""
    expired = datetime.now(tz=UTC) - timedelta(seconds=RUNNING_WINDOW_SECONDS + 60)
    append(_entry(run_id="r1", timestamp=expired))
    assert running_now() == []


# ---- is_role_running --------------------------------------------------------


def test_is_role_running_matches_project_and_role() -> None:
    append(_entry(run_id="r1", project="demo_five", agents=("engineer",)))
    assert is_role_running("demo_five", "engineer") is True
    assert is_role_running("demo_five", "manager") is False
    assert is_role_running("Demo", "engineer") is False


# ---- history_for_role -------------------------------------------------------


def test_history_for_role_returns_newest_first() -> None:
    now = datetime.now(tz=UTC)
    append(_entry(run_id="r0", project="p", agents=("manager",), timestamp=now - timedelta(hours=2)))
    append(_entry(run_id="r1", project="p", agents=("manager",), timestamp=now - timedelta(hours=1)))
    append(_entry(run_id="r2", project="p", agents=("engineer",), timestamp=now))

    history = history_for_role("p", "manager")
    assert len(history) == 2
    assert history[0].run_id == "r1"  # newest first


def test_history_for_role_respects_limit() -> None:
    now = datetime.now(tz=UTC)
    for i in range(30):
        append(_entry(run_id=f"r{i}", timestamp=now - timedelta(minutes=i)))
    assert len(history_for_role("p", "manager", limit=5)) == 5


# ---- crew_run context manager ----------------------------------------------


def test_crew_run_emits_start_and_finish_on_success() -> None:
    with crew_run(crew="planning", project="p", agents=["manager"]) as run_id:
        assert run_id  # uuid hex
    log = read_log()
    assert [e.event for e in log] == ["crew_started", "crew_finished"]
    assert log[0].run_id == log[1].run_id


def test_crew_run_emits_start_and_failed_on_exception() -> None:
    with pytest.raises(RuntimeError, match="boom"):
        with crew_run(crew="planning", project="p", agents=["manager"]):
            raise RuntimeError("boom")
    log = read_log()
    assert [e.event for e in log] == ["crew_started", "crew_failed"]
    assert log[1].error == "boom"


def test_crew_run_with_decision_id() -> None:
    with crew_run(
        crew="engineer", project="p", agents=["engineer"], decision_id="dec-1"
    ):
        pass
    log = read_log()
    assert log[0].decision_id == "dec-1"
    assert log[1].decision_id == "dec-1"


def test_crew_run_yields_unique_ids() -> None:
    with crew_run(crew="planning", project="p", agents=["manager"]) as id1:
        pass
    with crew_run(crew="planning", project="p", agents=["manager"]) as id2:
        pass
    assert id1 != id2
