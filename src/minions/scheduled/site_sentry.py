"""Site Sentry — continuous synthetic monitoring of every managed project.

Walks every active manifest with a non-empty ``deploy.production_url`` and
runs the same HTTP probes the post-deploy verifier uses, only on a fixed
cadence. Persists per-(project, check_path) samples, gates the existing
notifier on consecutive failure + dedup window, and emits exactly one
"all clear" on recovery.

See ``openspec/changes/ops-site-sentry`` for the spec.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from minions.deployments.verifier import run_health_checks
from minions.models.deployment import DeploymentRecord
from minions.models.manifest import Manifest, load_active_manifests
from minions.notify.base import Notifier
from minions.sentry.site_health_store import AlertState, SiteHealthSample
from minions.sentry.site_health_store_factory import SiteHealthStoreLike

logger = logging.getLogger(__name__)

# Defaults documented in openspec/changes/ops-site-sentry/proposal.md §
# Open Questions. Override via CLI flags / kwargs at the call site.
DEFAULT_FAIL_THRESHOLD = 2
DEFAULT_RECOVER_THRESHOLD = 2
DEFAULT_DEDUP_WINDOW = timedelta(minutes=30)


@dataclass(frozen=True)
class CheckOutcome:
    """One check's outcome in this sentry run."""

    project: str
    check_path: str
    ok: bool
    status_code: int | None
    latency_ms: int | None
    error: str | None
    consecutive_failures: int
    consecutive_successes: int
    alert_emitted: str | None  # "down" | "recovered" | None


@dataclass
class SiteSentryReport:
    """Per-run summary. ``alerts_emitted`` / ``alerts_suppressed`` make
    cron output debuggable + cheap to grep."""

    outcomes: list[CheckOutcome] = field(default_factory=list)
    projects_probed: int = 0
    samples_recorded: int = 0
    alerts_emitted: int = 0
    alerts_suppressed: int = 0
    skipped_projects: list[str] = field(default_factory=list)

    def to_markdown(self) -> str:
        lines = ["# Site Sentry", ""]
        lines.append(
            f"_Probed {self.projects_probed} project(s); recorded "
            f"{self.samples_recorded} sample(s); emitted "
            f"{self.alerts_emitted}, suppressed {self.alerts_suppressed}._"
        )
        if not self.outcomes:
            lines.append("")
            lines.append("No probes ran. Nothing to do.")
            return "\n".join(lines)
        lines.append("")
        for o in self.outcomes:
            badge = "✓" if o.ok else "✗"
            note = f" → ALERT ({o.alert_emitted})" if o.alert_emitted else ""
            extra = (
                f"status={o.status_code} latency={o.latency_ms}ms"
                if o.ok
                else f"status={o.status_code} error={o.error!r}"
            )
            lines.append(f"- {badge} **{o.project}** `{o.check_path}` — {extra}{note}")
        if self.skipped_projects:
            lines.append("")
            lines.append(
                "Skipped (no `deploy.production_url`): " + ", ".join(self.skipped_projects)
            )
        return "\n".join(lines)


def run_site_sentry(
    *,
    projects_dir: Path,
    site_health_store: SiteHealthStoreLike,
    notifier: Notifier,
    fail_threshold: int = DEFAULT_FAIL_THRESHOLD,
    recover_threshold: int = DEFAULT_RECOVER_THRESHOLD,
    dedup_window: timedelta = DEFAULT_DEDUP_WINDOW,
    dry_run: bool = False,
    now: datetime | None = None,
    manifests_override: dict[str, Manifest] | None = None,
) -> SiteSentryReport:
    """Probe every manifest with a configured production_url, persist
    samples, and emit transition alerts gated by threshold + dedup window.

    ``manifests_override`` lets tests inject a controlled set without
    touching the filesystem.
    """
    now = now or datetime.now(tz=UTC)
    manifests = (
        manifests_override
        if manifests_override is not None
        else load_active_manifests(projects_dir)
    )
    report = SiteSentryReport()

    for name, manifest in sorted(manifests.items()):
        cfg = manifest.deploy
        if not cfg.production_url:
            report.skipped_projects.append(name)
            continue
        report.projects_probed += 1
        record = DeploymentRecord(
            project=name,
            merge_sha="sentry",  # synthetic — verifier only uses this for logging
            deploy_target=cfg.target or "generic",
            production_url=cfg.production_url,
        )
        try:
            run_health_checks(config=cfg, record=record)
        except Exception as e:  # noqa: BLE001 — probes must never crash the loop
            logger.warning("site_sentry: run_health_checks crashed for %s: %s", name, e)
            continue

        for result in record.health_check_results:
            check_path = _path_from_url(result.url, cfg.production_url)
            sample = SiteHealthSample(
                project=name,
                check_path=check_path,
                ts=now,
                ok=result.ok,
                status_code=result.actual_status,
                latency_ms=result.latency_ms,
                error=result.error,
            )
            site_health_store.record(sample)
            report.samples_recorded += 1

            outcome = _classify_and_maybe_alert(
                store=site_health_store,
                notifier=notifier,
                sample=sample,
                fail_threshold=fail_threshold,
                recover_threshold=recover_threshold,
                dedup_window=dedup_window,
                now=now,
                dry_run=dry_run,
                production_url=cfg.production_url,
            )
            report.outcomes.append(outcome)
            if outcome.alert_emitted:
                report.alerts_emitted += 1
            elif _would_have_alerted(outcome):
                report.alerts_suppressed += 1

    return report


def _classify_and_maybe_alert(
    *,
    store: SiteHealthStoreLike,
    notifier: Notifier,
    sample: SiteHealthSample,
    fail_threshold: int,
    recover_threshold: int,
    dedup_window: timedelta,
    now: datetime,
    dry_run: bool,
    production_url: str,
) -> CheckOutcome:
    """Compute streaks from history; emit at-most-one alert per run.

    Alert kinds: ``down`` when the failing-streak crosses the threshold
    AND no recent down-alert exists in the dedup window; ``recovered``
    when an open down-alert is followed by ``recover_threshold``
    consecutive successes (sent exactly once).
    """
    history = store.list_recent_for_check(
        sample.project, sample.check_path, limit=max(fail_threshold, recover_threshold) + 1
    )
    # ``history`` already includes the just-recorded sample (newest first).
    consec_fail = _streak(history, ok=False)
    consec_ok = _streak(history, ok=True)
    state = store.get_alert_state(sample.project, sample.check_path)
    alert_emitted: str | None = None

    if not sample.ok and consec_fail >= fail_threshold:
        # Down — emit unless a down-alert is still inside the dedup window.
        recent_down_alert = (
            state is not None
            and state.last_alert_kind == "down"
            and now - state.last_alert_at < dedup_window
        )
        if not recent_down_alert and not dry_run:
            _send(
                notifier,
                subject=f"[site sentry] {sample.project} {sample.check_path} DOWN",
                body=_alert_body(sample, kind="down", production_url=production_url),
            )
            store.set_alert_state(
                AlertState(
                    project=sample.project,
                    check_path=sample.check_path,
                    last_alert_at=now,
                    last_alert_kind="down",
                )
            )
            alert_emitted = "down"
    elif (
        sample.ok
        and state is not None
        and state.last_alert_kind == "down"
        and consec_ok >= recover_threshold
    ):
        # Recovered — emit exactly once, then clear the open state.
        if not dry_run:
            _send(
                notifier,
                subject=f"[site sentry] {sample.project} {sample.check_path} RECOVERED",
                body=_alert_body(sample, kind="recovered", production_url=production_url),
            )
            store.set_alert_state(
                AlertState(
                    project=sample.project,
                    check_path=sample.check_path,
                    last_alert_at=now,
                    last_alert_kind="recovered",
                )
            )
            alert_emitted = "recovered"

    return CheckOutcome(
        project=sample.project,
        check_path=sample.check_path,
        ok=sample.ok,
        status_code=sample.status_code,
        latency_ms=sample.latency_ms,
        error=sample.error,
        consecutive_failures=consec_fail,
        consecutive_successes=consec_ok,
        alert_emitted=alert_emitted,
    )


def _streak(history: list[SiteHealthSample], *, ok: bool) -> int:
    """Count leading samples matching ``ok``. ``history`` is newest-first."""
    n = 0
    for s in history:
        if s.ok is ok:
            n += 1
        else:
            break
    return n


def _would_have_alerted(outcome: CheckOutcome) -> bool:
    """Did this run cross a threshold but get suppressed (dedup or dry-run)?"""
    if outcome.alert_emitted is not None:
        return False
    return (not outcome.ok) and outcome.consecutive_failures >= DEFAULT_FAIL_THRESHOLD


def _path_from_url(url: str, production_url: str) -> str:
    """Derive the check_path from the probed URL. Image-asset probes
    (from ``check_image_assets``) keep their full path so they're
    distinguishable in the dashboard."""
    parsed = urlparse(url)
    base_parsed = urlparse(production_url)
    if parsed.netloc and parsed.netloc != base_parsed.netloc:
        return url  # cross-host (e.g. CDN) — full URL is more useful than a path
    return parsed.path or "/"


def _alert_body(sample: SiteHealthSample, *, kind: str, production_url: str) -> str:
    full_url = production_url.rstrip("/") + sample.check_path
    if kind == "down":
        return (
            f"Site Sentry detected {sample.project} {sample.check_path} is DOWN.\n\n"
            f"URL:     {full_url}\n"
            f"Status:  {sample.status_code}\n"
            f"Latency: {sample.latency_ms} ms\n"
            f"Error:   {sample.error!r}\n"
            f"Time:    {sample.ts.isoformat()}\n\n"
            f"Dashboard: /sentry?project={sample.project}\n"
        )
    return (
        f"Site Sentry: {sample.project} {sample.check_path} has RECOVERED.\n\n"
        f"URL:     {full_url}\n"
        f"Status:  {sample.status_code}\n"
        f"Latency: {sample.latency_ms} ms\n"
        f"Time:    {sample.ts.isoformat()}\n"
    )


def _send(notifier: Notifier, *, subject: str, body: str) -> None:
    """Route an alert through the existing Notifier protocol. Swallows
    notifier failures — observability must never crash the loop."""
    try:
        notifier.notify_text(subject=subject, body=body)
    except Exception as e:  # noqa: BLE001
        logger.warning("site_sentry: notifier failed for %r: %s", subject, e)
