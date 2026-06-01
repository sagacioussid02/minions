"""Orchestrator tests for ``scheduled/site_sentry.py``.

The HTTP probe (`run_health_checks`) is monkeypatched so tests are
deterministic and offline. We drive the classification + alert state
machine by controlling the sequence of probe outcomes for one (project,
check_path) over multiple sentry runs.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from minions.models.deployment import HealthCheckResult
from minions.models.manifest import (
    DeployConfig,
    HealthCheck,
    Manifest,
    ManifestSource,
)
from minions.scheduled import site_sentry as ss
from minions.scheduled.site_sentry import run_site_sentry
from minions.sentry.site_health_store import SiteHealthStore

# ---- helpers ----------------------------------------------------------------


class RecorderNotifier:
    """Test notifier that records every call."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def notify_approval_request(self, decision: Any) -> None: ...
    def notify_decision_resolved(self, decision: Any) -> None: ...

    def notify_text(self, *, subject: str, body: str) -> None:
        self.calls.append((subject, body))


def _manifest(name: str = "demo_three") -> Manifest:
    return Manifest(
        name=name,
        description="t",
        owner="me@test",
        source=ManifestSource(kind="github", repo=f"owner/{name}"),
        weekly_budget_usd=1.0,
        monthly_budget_usd=4.0,
        deploy=DeployConfig(
            target="generic",
            production_url=f"https://{name}.test",
            health_checks=[HealthCheck(path="/")],
            check_image_assets=False,  # keep probes simple
        ),
    )


def _result(
    *, ok: bool, status: int | None, latency: int = 12, err: str | None = None
) -> HealthCheckResult:
    return HealthCheckResult(
        url="https://demo_three.test/",
        kind="path",
        expected_status=200,
        actual_status=status,
        latency_ms=latency,
        error=err,
        ok=ok,
    )


def _stub_health_checks(outcomes_by_run: list[list[HealthCheckResult]]):
    """Build a monkeypatch target that returns the next planned outcome list
    on each call to ``run_health_checks``. One call per project per sentry run.
    """
    state = {"i": 0}

    def _stub(*, config, record):
        results = outcomes_by_run[state["i"]]
        state["i"] += 1
        record.health_check_results.extend(results)
        return record

    return _stub


def _run(store, notifier, *, manifests, monkeypatch, outcomes_by_run, now, **kwargs):
    """Convenience: monkeypatch run_health_checks and invoke run_site_sentry."""
    monkeypatch.setattr(ss, "run_health_checks", _stub_health_checks(outcomes_by_run))
    return run_site_sentry(
        projects_dir=Path("/unused"),
        site_health_store=store,
        notifier=notifier,
        manifests_override=manifests,
        now=now,
        **kwargs,
    )


# ---- happy path -------------------------------------------------------------


def test_happy_run_records_sample_no_alert(tmp_path, monkeypatch):
    store = SiteHealthStore(tmp_path / "sh.json")
    notifier = RecorderNotifier()
    report = _run(
        store,
        notifier,
        manifests={"demo_three": _manifest()},
        monkeypatch=monkeypatch,
        outcomes_by_run=[[_result(ok=True, status=200)]],
        now=datetime.now(tz=UTC),
    )
    assert report.samples_recorded == 1
    assert report.alerts_emitted == 0
    assert notifier.calls == []


# ---- threshold gating -------------------------------------------------------


def test_single_failure_below_threshold_no_alert(tmp_path, monkeypatch):
    store = SiteHealthStore(tmp_path / "sh.json")
    notifier = RecorderNotifier()
    report = _run(
        store,
        notifier,
        manifests={"demo_three": _manifest()},
        monkeypatch=monkeypatch,
        outcomes_by_run=[[_result(ok=False, status=503, err="503")]],
        now=datetime.now(tz=UTC),
    )
    assert report.alerts_emitted == 0
    assert notifier.calls == []


def test_two_consecutive_failures_alerts_once(tmp_path, monkeypatch):
    store = SiteHealthStore(tmp_path / "sh.json")
    notifier = RecorderNotifier()
    t0 = datetime.now(tz=UTC)

    # Run 1: failure → below threshold, no alert.
    _run(
        store,
        notifier,
        manifests={"demo_three": _manifest()},
        monkeypatch=monkeypatch,
        outcomes_by_run=[[_result(ok=False, status=503, err="503")]],
        now=t0,
    )
    # Run 2: another failure → threshold trips → 1 alert.
    monkeypatch.setattr(
        ss, "run_health_checks", _stub_health_checks([[_result(ok=False, status=503, err="503")]])
    )
    run_site_sentry(
        projects_dir=Path("/unused"),
        site_health_store=store,
        notifier=notifier,
        manifests_override={"demo_three": _manifest()},
        now=t0 + timedelta(minutes=10),
    )
    assert len(notifier.calls) == 1
    subject, body = notifier.calls[0]
    assert "DOWN" in subject and "demo_three" in subject and "/" in subject
    assert "demo_three.test" in body  # production_url referenced


def test_sustained_failure_inside_dedup_window_no_second_alert(tmp_path, monkeypatch):
    store = SiteHealthStore(tmp_path / "sh.json")
    notifier = RecorderNotifier()
    t0 = datetime.now(tz=UTC)

    # Two failures → 1 alert at t0.
    monkeypatch.setattr(
        ss, "run_health_checks", _stub_health_checks([[_result(ok=False, status=503)]])
    )
    run_site_sentry(
        projects_dir=Path("/unused"),
        site_health_store=store,
        notifier=notifier,
        manifests_override={"demo_three": _manifest()},
        now=t0,
    )
    monkeypatch.setattr(
        ss, "run_health_checks", _stub_health_checks([[_result(ok=False, status=503)]])
    )
    run_site_sentry(
        projects_dir=Path("/unused"),
        site_health_store=store,
        notifier=notifier,
        manifests_override={"demo_three": _manifest()},
        now=t0 + timedelta(minutes=5),
    )
    assert len(notifier.calls) == 1  # threshold-trip alert

    # Run 3: still failing, well inside the default 30-min dedup window → suppressed.
    monkeypatch.setattr(
        ss, "run_health_checks", _stub_health_checks([[_result(ok=False, status=503)]])
    )
    report = run_site_sentry(
        projects_dir=Path("/unused"),
        site_health_store=store,
        notifier=notifier,
        manifests_override={"demo_three": _manifest()},
        now=t0 + timedelta(minutes=15),
    )
    assert len(notifier.calls) == 1
    assert report.alerts_suppressed >= 1


# ---- recovery ---------------------------------------------------------------


def test_recovery_emits_all_clear_once_then_silent(tmp_path, monkeypatch):
    store = SiteHealthStore(tmp_path / "sh.json")
    notifier = RecorderNotifier()
    t0 = datetime.now(tz=UTC)

    # Two failures → 1 down-alert.
    for i in range(2):
        monkeypatch.setattr(
            ss, "run_health_checks", _stub_health_checks([[_result(ok=False, status=503)]])
        )
        run_site_sentry(
            projects_dir=Path("/unused"),
            site_health_store=store,
            notifier=notifier,
            manifests_override={"demo_three": _manifest()},
            now=t0 + timedelta(minutes=i * 5),
        )
    assert len(notifier.calls) == 1

    # Two consecutive successes → 1 recovered-alert.
    for i in range(2):
        monkeypatch.setattr(
            ss, "run_health_checks", _stub_health_checks([[_result(ok=True, status=200)]])
        )
        run_site_sentry(
            projects_dir=Path("/unused"),
            site_health_store=store,
            notifier=notifier,
            manifests_override={"demo_three": _manifest()},
            now=t0 + timedelta(minutes=20 + i * 5),
        )
    subjects = [c[0] for c in notifier.calls]
    assert sum("RECOVERED" in s for s in subjects) == 1

    # Next success: nothing new.
    monkeypatch.setattr(
        ss, "run_health_checks", _stub_health_checks([[_result(ok=True, status=200)]])
    )
    run_site_sentry(
        projects_dir=Path("/unused"),
        site_health_store=store,
        notifier=notifier,
        manifests_override={"demo_three": _manifest()},
        now=t0 + timedelta(minutes=40),
    )
    assert sum("RECOVERED" in c[0] for c in notifier.calls) == 1


# ---- skip + dry-run ---------------------------------------------------------


def test_skips_project_without_production_url(tmp_path, monkeypatch):
    m = _manifest()
    m.deploy.production_url = None
    store = SiteHealthStore(tmp_path / "sh.json")
    notifier = RecorderNotifier()
    monkeypatch.setattr(ss, "run_health_checks", _stub_health_checks([]))
    report = run_site_sentry(
        projects_dir=Path("/unused"),
        site_health_store=store,
        notifier=notifier,
        manifests_override={"demo_three": m},
        now=datetime.now(tz=UTC),
    )
    assert report.projects_probed == 0
    assert report.skipped_projects == ["demo_three"]
    assert notifier.calls == []


def test_dry_run_persists_samples_but_never_alerts(tmp_path, monkeypatch):
    store = SiteHealthStore(tmp_path / "sh.json")
    notifier = RecorderNotifier()
    t0 = datetime.now(tz=UTC)

    # Two failures in a row — would normally trip an alert, but dry_run=True suppresses.
    for i in range(2):
        monkeypatch.setattr(
            ss, "run_health_checks", _stub_health_checks([[_result(ok=False, status=503)]])
        )
        run_site_sentry(
            projects_dir=Path("/unused"),
            site_health_store=store,
            notifier=notifier,
            manifests_override={"demo_three": _manifest()},
            now=t0 + timedelta(minutes=i * 5),
            dry_run=True,
        )
    assert notifier.calls == []
    # But samples are persisted.
    assert len(store.list_all_samples()) == 2


# ---- multi-project ----------------------------------------------------------


def test_independent_state_per_project(tmp_path, monkeypatch):
    store = SiteHealthStore(tmp_path / "sh.json")
    notifier = RecorderNotifier()
    t0 = datetime.now(tz=UTC)

    # demo_three fails twice; demo stays healthy. Only demo_three should alert.
    # ``run_site_sentry`` iterates manifests sorted alphabetically, so the
    # first stub outcome goes to ``demo`` (healthy) and the second to
    # ``demo_three`` (failing).
    manifests = {"demo_three": _manifest("demo_three"), "demo": _manifest("demo")}
    for _ in range(2):
        monkeypatch.setattr(
            ss,
            "run_health_checks",
            _stub_health_checks([[_result(ok=True, status=200)], [_result(ok=False, status=503)]]),
        )
        run_site_sentry(
            projects_dir=Path("/unused"),
            site_health_store=store,
            notifier=notifier,
            manifests_override=manifests,
            now=t0,
        )
        t0 += timedelta(minutes=10)

    subjects = [c[0] for c in notifier.calls]
    assert sum("demo_three" in s and "DOWN" in s for s in subjects) == 1
    assert not any("demo" in s for s in subjects)
