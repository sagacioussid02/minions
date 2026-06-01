"""JSON ``SiteHealthStore`` round-trip + ordering + alert-state tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from minions.sentry.site_health_store import AlertState, SiteHealthSample, SiteHealthStore


def _sample(
    *,
    project: str = "demo_three",
    check_path: str = "/",
    ok: bool = True,
    ts: datetime | None = None,
    status_code: int | None = 200,
    latency_ms: int | None = 42,
    error: str | None = None,
) -> SiteHealthSample:
    return SiteHealthSample(
        project=project,
        check_path=check_path,
        ts=ts or datetime.now(tz=UTC),
        ok=ok,
        status_code=status_code,
        latency_ms=latency_ms,
        error=error,
    )


def test_record_and_list_all_round_trip(tmp_path: Path) -> None:
    store = SiteHealthStore(tmp_path / "sh.json")
    store.record(_sample(project="demo_three"))
    store.record(_sample(project="demo", check_path="/api/health"))

    samples = store.list_all_samples()
    assert {s.project for s in samples} == {"demo_three", "demo"}


def test_list_recent_for_check_is_newest_first(tmp_path: Path) -> None:
    store = SiteHealthStore(tmp_path / "sh.json")
    now = datetime.now(tz=UTC)
    store.record(_sample(ts=now - timedelta(minutes=20)))
    store.record(_sample(ts=now - timedelta(minutes=10), ok=False, status_code=500))
    store.record(_sample(ts=now, status_code=200))

    recent = store.list_recent_for_check("demo_three", "/")
    assert [s.status_code for s in recent] == [200, 500, 200]  # newest -> oldest


def test_list_recent_filters_by_project_and_check_path(tmp_path: Path) -> None:
    store = SiteHealthStore(tmp_path / "sh.json")
    store.record(_sample(project="demo_three", check_path="/"))
    store.record(_sample(project="demo_three", check_path="/api"))
    store.record(_sample(project="demo", check_path="/"))

    only_demo_three_root = store.list_recent_for_check("demo_three", "/")
    assert len(only_demo_three_root) == 1
    assert only_demo_three_root[0].project == "demo_three"
    assert only_demo_three_root[0].check_path == "/"


def test_alert_state_round_trip(tmp_path: Path) -> None:
    store = SiteHealthStore(tmp_path / "sh.json")
    assert store.get_alert_state("demo_three", "/") is None

    now = datetime.now(tz=UTC)
    store.set_alert_state(
        AlertState(project="demo_three", check_path="/", last_alert_at=now, last_alert_kind="down")
    )
    got = store.get_alert_state("demo_three", "/")
    assert got is not None
    assert got.last_alert_kind == "down"
    assert got.last_alert_at == now


def test_alert_state_overwrites_on_recovery(tmp_path: Path) -> None:
    store = SiteHealthStore(tmp_path / "sh.json")
    t0 = datetime.now(tz=UTC)
    store.set_alert_state(AlertState("demo_three", "/", t0, "down"))
    store.set_alert_state(AlertState("demo_three", "/", t0 + timedelta(minutes=5), "recovered"))

    got = store.get_alert_state("demo_three", "/")
    assert got is not None
    assert got.last_alert_kind == "recovered"


def test_persists_across_instances(tmp_path: Path) -> None:
    path = tmp_path / "sh.json"
    SiteHealthStore(path).record(_sample(project="x"))
    again = SiteHealthStore(path).list_all_samples()
    assert len(again) == 1 and again[0].project == "x"
