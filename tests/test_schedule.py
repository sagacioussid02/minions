"""Tests for src/minions/dashboard/schedule.py."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from minions.dashboard.schedule import (
    ROLE_TO_CREWS,
    V0_FRUGAL_SCHEDULES,
    _next_fire,
    next_run_for_role,
)


# ---- crew schedule mapping is what we documented --------------------------


def test_crew_schedules_match_v0_frugal_cadence() -> None:
    assert V0_FRUGAL_SCHEDULES["weekly_planning"] == "0 9 * * 1"
    assert V0_FRUGAL_SCHEDULES["daily_monitor"] == "0 9 * * *"
    assert V0_FRUGAL_SCHEDULES["friday_digest"] == "0 16 * * 5"


# ---- planning roles fire weekly -------------------------------------------


@pytest.mark.parametrize("role", ["product_owner", "principal_engineer", "manager"])
def test_planning_roles_resolve_to_scheduled_run(role: str) -> None:
    # Saturday 2026-05-09 12:00 UTC → next Monday is 2026-05-11 09:00.
    sat = datetime(2026, 5, 9, 12, 0, tzinfo=UTC)
    nr = next_run_for_role(role, now=sat)
    assert nr.kind == "scheduled"
    assert nr.crew == "weekly_planning"
    assert nr.next_at == datetime(2026, 5, 11, 9, 0, tzinfo=UTC)


def test_planning_role_today_after_fire_resolves_to_next_week() -> None:
    """If we're past Mon 09:00, the next firing is the following Monday."""
    mon_after = datetime(2026, 5, 11, 12, 0, tzinfo=UTC)  # Mon 12:00
    nr = next_run_for_role("manager", now=mon_after)
    assert nr.next_at == datetime(2026, 5, 18, 9, 0, tzinfo=UTC)


# ---- engineering roles are event-driven -----------------------------------


@pytest.mark.parametrize("role", ["engineer", "senior_engineer", "tech_team_lead", "intern"])
def test_engineering_roles_are_on_approval(role: str) -> None:
    nr = next_run_for_role(role)
    assert nr.kind == "on_approval"
    assert nr.next_at is None
    assert "approved" in nr.description.lower()


# ---- shared layers ---------------------------------------------------------


@pytest.mark.parametrize(
    "role", ["ceo", "cto", "chief_auditor", "code_auditor", "devils_advocate"]
)
def test_unwired_shared_roles_are_not_scheduled(role: str) -> None:
    nr = next_run_for_role(role)
    assert nr.kind == "not_scheduled"


@pytest.mark.parametrize("role", ["cloud_devops", "devsecops", "team_architect"])
def test_specialist_consult_roles_are_on_demand(role: str) -> None:
    nr = next_run_for_role(role)
    assert nr.kind == "on_demand"


def test_unknown_role_falls_back_to_not_scheduled() -> None:
    nr = next_run_for_role("does_not_exist")
    assert nr.kind == "not_scheduled"


# ---- _next_fire cron logic ------------------------------------------------


def test_next_fire_daily_cron_today() -> None:
    now = datetime(2026, 5, 9, 8, 0, tzinfo=UTC)  # 8am
    # Daily 09:00 today
    assert _next_fire("0 9 * * *", now=now) == datetime(2026, 5, 9, 9, 0, tzinfo=UTC)


def test_next_fire_daily_cron_tomorrow_when_past() -> None:
    now = datetime(2026, 5, 9, 10, 0, tzinfo=UTC)  # past 09:00
    assert _next_fire("0 9 * * *", now=now) == datetime(2026, 5, 10, 9, 0, tzinfo=UTC)


def test_next_fire_friday_cron() -> None:
    # Wed 2026-05-13 12:00 → next Fri is 2026-05-15 16:00
    now = datetime(2026, 5, 13, 12, 0, tzinfo=UTC)
    assert _next_fire("0 16 * * 5", now=now) == datetime(2026, 5, 15, 16, 0, tzinfo=UTC)


def test_next_fire_returns_none_on_malformed() -> None:
    assert _next_fire("not a cron", now=datetime.now(tz=UTC)) is None
    assert _next_fire("a b * * *", now=datetime.now(tz=UTC)) is None


# ---- coverage check --------------------------------------------------------


def test_every_role_in_per_project_template_has_schedule_mapping() -> None:
    """No role used by the planning/engineer templates should be unmapped."""
    from minions.agents.roster import PER_PROJECT_TEMPLATE

    for role in PER_PROJECT_TEMPLATE:
        assert role.value in ROLE_TO_CREWS, f"{role.value} missing from ROLE_TO_CREWS"
