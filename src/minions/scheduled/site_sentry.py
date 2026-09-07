"""Site Sentry — continuous synthetic health + expiry monitoring.

Unlike ``post_deploy_verify`` (which probes once per merge and files a
revert Decision on failure), Site Sentry runs on a fixed cadence and
*persists a time series*. Every tick it walks each active manifest and:

  1. **Uptime** — runs the deterministic health-check probes (reusing the
     same verifier as post-deploy) and appends one ``site_health_samples``
     row per probe.
  2. **TLS cert expiry** — reads the certificate expiry off the HTTPS
     handshake (public data, no secret access) and stamps it on the samples.
  3. **Renewal radar** — mirrors each manifest's declared ``renewals``
     (licenses) and ``secret_rotations`` (credential rotations) into
     ``renewal_reminders`` (dates only — never the secret values).

The operator console's Sentry page reads the latest sample per
(project, check_path), 24h uptime/latency rollups, cert expiry, and the
renewal reminders straight off those tables.

No LLM. No GitHub writes. No Decisions. Cost: a handful of HTTP/TLS
requests per project per tick. Failures are caught per-project so one
down site never aborts the sweep.

Multi-tenancy: rows are written under a single tenant resolved from
``MINIONS_FOUNDER_TENANT_ID`` (the founder's own portfolio — the same id
the web layer pins via the matching env var).
"""

from __future__ import annotations

import logging
import os
import socket
import ssl
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from minions.db.connection import connect, has_database_url
from minions.deployments.verifier import run_health_checks
from minions.models.deployment import DeploymentRecord
from minions.models.manifest import HealthCheck, Manifest, load_active_manifests

logger = logging.getLogger(__name__)

# Default tenant when MINIONS_FOUNDER_TENANT_ID is unset. Keeps local/dry
# runs working; production pins the real founder id via env.
_DEFAULT_TENANT_ID = "founder"

# Renewal severity thresholds (days until due). Mirrored in the web layer.
_RED_DAYS = 7
_AMBER_DAYS = 30


def _tenant_id() -> str:
    return os.environ.get("MINIONS_FOUNDER_TENANT_ID") or _DEFAULT_TENANT_ID


# ---------------------------------------------------------------------------
# TLS certificate expiry.
# ---------------------------------------------------------------------------


def _cert_expiry(url: str, *, timeout: float = 10.0) -> datetime | None:
    """Return the TLS cert's notAfter for an ``https://`` URL, or None.

    Reads only the public certificate presented in the handshake — no
    request body, no secret material. Any failure (non-https, DNS, timeout,
    handshake error) returns None rather than raising.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        return None
    host = parsed.hostname
    port = parsed.port or 443
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                cert = ssock.getpeercert()
        not_after = cert.get("notAfter") if cert else None
        if not isinstance(not_after, str):
            return None
        # e.g. "Jun  1 12:00:00 2026 GMT"
        return datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=UTC)
    except (OSError, ssl.SSLError, ValueError) as e:
        logger.debug("cert-expiry probe failed for %s: %s", url, e)
        return None


class ProbeSample(BaseModel):
    """One persisted health probe (mirrors a site_health_samples row)."""

    check_path: str
    ok: bool
    status_code: int | None = None
    latency_ms: int | None = None
    error: str | None = None
    cert_expires_at: datetime | None = None


class SiteSentryOutcome(BaseModel):
    project: str
    status: str  # "probed" | "skipped" | "error"
    samples: list[ProbeSample] = Field(default_factory=list)
    reason: str | None = None

    @property
    def healthy(self) -> int:
        return sum(1 for s in self.samples if s.ok)

    @property
    def unhealthy(self) -> int:
        return sum(1 for s in self.samples if not s.ok)


# ---------------------------------------------------------------------------
# Renewal radar.
# ---------------------------------------------------------------------------


def _renewal_severity(days_until: int) -> str:
    if days_until < 0:
        return "overdue"
    if days_until <= _RED_DAYS:
        return "red"
    if days_until <= _AMBER_DAYS:
        return "amber"
    return "ok"


class RenewalStatus(BaseModel):
    """A declared renewal/rotation with severity computed against a date."""

    project: str
    kind: str  # "license" | "secret_rotation"
    name: str
    due: date
    url: str | None = None
    note: str | None = None
    days_until: int
    severity: str  # "ok" | "amber" | "red" | "overdue"


def renewal_statuses(
    manifests: dict[str, Manifest], *, today: date | None = None
) -> list[RenewalStatus]:
    """Flatten every manifest's declared renewals/rotations with severity.

    Sorted by due date (soonest first). Pure — no I/O — so both the Sentry
    collector and the Friday digest share one source of truth.
    """
    ref = today or datetime.now(tz=UTC).date()
    out: list[RenewalStatus] = []
    for manifest in manifests.values():
        for kind, items in (
            ("license", manifest.renewals),
            ("secret_rotation", manifest.secret_rotations),
        ):
            for it in items:
                days = (it.due - ref).days
                out.append(
                    RenewalStatus(
                        project=manifest.name,
                        kind=kind,
                        name=it.name,
                        due=it.due,
                        url=it.url,
                        note=it.note,
                        days_until=days,
                        severity=_renewal_severity(days),
                    )
                )
    out.sort(key=lambda r: r.due)
    return out


class SiteSentryReport(BaseModel):
    started_at: str
    finished_at: str
    tenant_id: str
    persisted: bool
    outcomes: list[SiteSentryOutcome] = Field(default_factory=list)
    renewals: list[RenewalStatus] = Field(default_factory=list)

    @property
    def projects_probed(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "probed")

    @property
    def samples_written(self) -> int:
        return sum(len(o.samples) for o in self.outcomes)

    @property
    def renewals_due_soon(self) -> int:
        return sum(1 for r in self.renewals if r.severity != "ok")


def _configured_check_paths(manifest: Manifest) -> list[str]:
    """The check paths the verifier will probe, in order.

    Mirrors ``run_health_checks``: an empty ``health_checks`` list defaults
    to a single ``GET /``. Used to map the returned path-probe results back
    to their manifest ``check_path``.
    """
    checks = manifest.deploy.health_checks or [HealthCheck(path="/")]
    return [c.path for c in checks]


def _probe_project(manifest: Manifest) -> SiteSentryOutcome:
    project = manifest.name
    cfg = manifest.deploy
    if not cfg.production_url:
        return SiteSentryOutcome(
            project=project, status="skipped", reason="no deploy.production_url"
        )

    record = DeploymentRecord(
        project=project,
        merge_sha="synthetic",  # Site Sentry is not tied to a merge.
        deploy_target=cfg.target or "generic",
        production_url=cfg.production_url,
    )
    run_health_checks(config=cfg, record=record)

    # One cert-expiry read per project (same host across all path probes).
    cert_expires_at = _cert_expiry(cfg.production_url)

    # Keep only the configured-path probes (drop image-asset probes) and map
    # each back to its manifest check_path by position.
    paths = _configured_check_paths(manifest)
    path_results = [r for r in record.health_check_results if r.kind == "path"]
    samples: list[ProbeSample] = []
    for i, r in enumerate(path_results):
        check_path = paths[i] if i < len(paths) else r.url
        samples.append(
            ProbeSample(
                check_path=check_path,
                ok=r.ok,
                status_code=r.actual_status,
                latency_ms=r.latency_ms,
                error=r.error,
                cert_expires_at=cert_expires_at,
            )
        )
    return SiteSentryOutcome(project=project, status="probed", samples=samples)


def _persist_samples(tenant_id: str, outcomes: list[SiteSentryOutcome], ts: datetime) -> None:
    rows = [
        (
            tenant_id,
            o.project,
            s.check_path,
            ts,
            s.ok,
            s.status_code,
            s.latency_ms,
            s.error,
            s.cert_expires_at,
        )
        for o in outcomes
        if o.status == "probed"
        for s in o.samples
    ]
    if not rows:
        return
    with connect() as conn, conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO site_health_samples
                (tenant_id, project, check_path, ts, ok, status_code,
                 latency_ms, error, cert_expires_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            rows,
        )


def _persist_renewals(
    tenant_id: str, manifests: dict[str, Manifest], renewals: list[RenewalStatus]
) -> None:
    """Replace-per-project sync of declared renewals into renewal_reminders.

    Deleting a project's rows first means removing an item from a manifest
    clears its reminder on the next tick. Only projects we actually walked
    this tick are touched, so a project temporarily filtered out (``-p``)
    keeps its rows.
    """
    by_project: dict[str, list[RenewalStatus]] = {}
    for r in renewals:
        by_project.setdefault(r.project, []).append(r)

    with connect() as conn, conn.cursor() as cur:
        for project in manifests:
            cur.execute(
                "DELETE FROM renewal_reminders WHERE tenant_id = %s AND project = %s",
                (tenant_id, project),
            )
            items = by_project.get(project, [])
            if items:
                cur.executemany(
                    """
                    INSERT INTO renewal_reminders
                        (tenant_id, project, kind, name, due, url, note, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                    """,
                    [(tenant_id, r.project, r.kind, r.name, r.due, r.url, r.note) for r in items],
                )


def run_site_sentry(
    *,
    projects_dir: Path,
    dry_run: bool = True,
    projects: list[str] | None = None,
    scope: str = "all",
) -> SiteSentryReport:
    """Probe production URLs + mirror renewals; persist unless ``dry_run``.

    ``scope`` splits the work so the two halves can run on different cadences
    (they cost very different amounts of GitHub-Actions time):

      * ``"health"``  — probe each site (uptime + TLS cert), write samples.
        This is the frequent, HTTP-heavy half.
      * ``"renewals"`` — mirror declared license/rotation dates into
        ``renewal_reminders``. Cheap; daily granularity is plenty.
      * ``"all"`` (default) — both, for manual/dry runs.

    ``dry_run`` does the work but never writes to Postgres. Persistence is
    also skipped (with a warning) when no database URL resolves.
    """
    do_health = scope in ("all", "health")
    do_renewals = scope in ("all", "renewals")

    started = datetime.now(tz=UTC)
    tenant_id = _tenant_id()
    manifests = load_active_manifests(projects_dir)
    if projects:
        wanted = {p.lower() for p in projects}
        manifests = {n: m for n, m in manifests.items() if n.lower() in wanted}

    outcomes: list[SiteSentryOutcome] = []
    if do_health:
        for name, manifest in manifests.items():
            try:
                outcomes.append(_probe_project(manifest))
            except Exception as e:  # noqa: BLE001 — one bad project must not abort the sweep
                logger.exception("site-sentry probe failed for %s", name)
                outcomes.append(
                    SiteSentryOutcome(
                        project=name, status="error", reason=f"{type(e).__name__}: {e}"
                    )
                )

    renewals = renewal_statuses(manifests, today=started.date()) if do_renewals else []

    persisted = False
    if not dry_run:
        if has_database_url():
            if do_health:
                _persist_samples(tenant_id, outcomes, started)
            if do_renewals:
                _persist_renewals(tenant_id, manifests, renewals)
            persisted = True
        else:
            logger.warning("site-sentry: no database URL resolved — nothing persisted")

    return SiteSentryReport(
        started_at=started.isoformat(),
        finished_at=datetime.now(tz=UTC).isoformat(),
        tenant_id=tenant_id,
        persisted=persisted,
        outcomes=outcomes,
        renewals=renewals,
    )
