"""Seed the Sentry page with realistic demo data for a live walkthrough.

Backfills 24h of ``site_health_samples`` (so the p50/p99/uptime rollups look
real, not like a single fresh probe) plus a spread of ``renewal_reminders``
and TLS cert-expiry states across a few demo projects. Everything is written
under one tenant so the operator console renders a populated, believable
Sentry page for a screen recording or investor demo.

This is a *demo* seed — it writes synthetic rows. It never reads secrets and
never touches any real project. Safe to re-run: it clears its own demo
projects' rows first (idempotent).

Usage::

    # Requires MINIONS_DATABASE_URL (Neon) in the environment.
    python scripts/seed_sentry_demo.py                 # seed under "founder"
    python scripts/seed_sentry_demo.py --tenant <uuid> # or a specific tenant
    python scripts/seed_sentry_demo.py --clear-only     # remove the demo rows

The tenant defaults to MINIONS_FOUNDER_TENANT_ID, else "founder" — matching
the Site Sentry collector so the seeded rows show up for the same operator.
"""

from __future__ import annotations

import argparse
import os
import random
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import UUID

from minions.db.connection import connect, has_database_url
from minions.db.migrate import apply_migrations

# Deterministic so re-runs and screenshots are stable.
_RNG = random.Random(1729)

_SAMPLE_INTERVAL_MIN = 10
_WINDOW_HOURS = 24

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _read_env_key(path: Path, key: str) -> str | None:
    """Pull one KEY=value out of a .env-style file, or None. No deps."""
    if not path.is_file():
        return None
    try:
        for raw in path.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() == key:
                return v.strip().strip("'\"") or None
    except OSError:
        return None
    return None


def _resolve_tenant(cli_value: str | None) -> str:
    """CLI flag → env → web/.env.local → root .env → 'founder' fallback.

    The founder tenant id is a UUID in the SaaS/Neon schema; the web pins it
    via MINIONS_FOUNDER_TENANT_ID (in web/.env.local). This mirrors that so
    the seed lands under the same tenant the console resolves for you.
    """
    if cli_value:
        return cli_value
    from_env = os.environ.get("MINIONS_FOUNDER_TENANT_ID")
    if from_env:
        return from_env
    for candidate in (_REPO_ROOT / "web" / ".env.local", _REPO_ROOT / ".env"):
        found = _read_env_key(candidate, "MINIONS_FOUNDER_TENANT_ID")
        if found:
            return found
    return "founder"


@dataclass(frozen=True)
class DemoCheck:
    path: str
    base_latency_ms: int
    # Fraction of ticks that fail, and whether failures cluster into one
    # "incident" window (more realistic than random scatter).
    fail_rate: float = 0.0
    incident: bool = False


@dataclass(frozen=True)
class DemoProject:
    name: str
    checks: tuple[DemoCheck, ...]
    cert_days: int | None  # days until TLS cert expiry (None = no https)


@dataclass(frozen=True)
class DemoRenewal:
    project: str
    kind: str  # "license" | "secret_rotation"
    name: str
    due_days: int  # relative to today; negative = overdue
    url: str | None = None
    note: str | None = None


_PROJECTS: tuple[DemoProject, ...] = (
    DemoProject(
        name="Aurora",
        checks=(
            DemoCheck("/", 180),
            DemoCheck("/api/health", 90),
            DemoCheck("/api/status", 110),
        ),
        cert_days=142,  # healthy
    ),
    DemoProject(
        name="Beacon",
        checks=(
            DemoCheck("/", 240),
            DemoCheck("/api/health", 130, fail_rate=0.06, incident=True),
        ),
        cert_days=19,  # amber
    ),
    DemoProject(
        name="Cinder",
        checks=(
            DemoCheck("/", 320, fail_rate=0.02),
            DemoCheck("/checkout", 410),
        ),
        cert_days=4,  # red
    ),
)

_RENEWALS: tuple[DemoRenewal, ...] = (
    DemoRenewal(
        "Aurora", "license", "Vercel Pro", 58, "https://vercel.com/account/plans", "annual"
    ),
    DemoRenewal("Aurora", "secret_rotation", "STRIPE_SECRET_KEY", 26, note="quarterly"),
    DemoRenewal("Beacon", "license", "Datadog", 12, "https://app.datadoghq.com/billing", "monthly"),
    DemoRenewal("Cinder", "secret_rotation", "ANTHROPIC_API_KEY", 3, note="rotate quarterly"),
    DemoRenewal("Cinder", "license", "Domain cinder.app", -2, note="auto-renew failed"),
)


def _demo_project_names() -> list[str]:
    return [p.name for p in _PROJECTS]


def _clear(cur, tenant_id: str) -> None:
    names = _demo_project_names()
    cur.execute(
        "DELETE FROM site_health_samples WHERE tenant_id = %s AND project = ANY(%s)",
        (tenant_id, names),
    )
    cur.execute(
        "DELETE FROM renewal_reminders WHERE tenant_id = %s AND project = ANY(%s)",
        (tenant_id, names),
    )


def _sample_rows(tenant_id: str, now: datetime) -> list[tuple]:
    rows: list[tuple] = []
    ticks = (_WINDOW_HOURS * 60) // _SAMPLE_INTERVAL_MIN
    for project in _PROJECTS:
        cert_at = (
            now + timedelta(days=project.cert_days) if project.cert_days is not None else None
        )
        for check in project.checks:
            # One clustered incident window if requested.
            incident_start = _RNG.randint(0, ticks - 12) if check.incident else None
            for i in range(ticks):
                ts = now - timedelta(minutes=_SAMPLE_INTERVAL_MIN * (ticks - i))
                in_incident = incident_start is not None and incident_start <= i < incident_start + 8
                ok = False if in_incident else _RNG.random() >= check.fail_rate
                if ok:
                    jitter = _RNG.randint(-30, 60)
                    latency = max(20, check.base_latency_ms + jitter)
                    status, err = 200, None
                else:
                    latency = _RNG.randint(1000, 4000)
                    status, err = (503, "expected 200, got 503")
                rows.append(
                    (
                        tenant_id,
                        project.name,
                        check.path,
                        ts,
                        ok,
                        status,
                        latency,
                        err,
                        cert_at,
                    )
                )
    return rows


def _renewal_rows(tenant_id: str, today: date) -> list[tuple]:
    return [
        (
            tenant_id,
            r.project,
            r.kind,
            r.name,
            today + timedelta(days=r.due_days),
            r.url,
            r.note,
        )
        for r in _RENEWALS
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tenant",
        default=None,
        help="Tenant id to seed under. Default: MINIONS_FOUNDER_TENANT_ID "
        "(env → web/.env.local → .env), else 'founder'.",
    )
    parser.add_argument(
        "--clear-only",
        action="store_true",
        help="Delete the demo projects' rows and exit (no seeding).",
    )
    args = parser.parse_args()

    tenant = _resolve_tenant(args.tenant)

    if not has_database_url():
        print("✗ No database URL resolved. Set MINIONS_DATABASE_URL and retry.")
        return 1

    # The SaaS/Neon schema types tenant_id as UUID. A non-UUID value (e.g. the
    # "founder" fallback) only works against a fresh fork where migration 0010
    # created the column as TEXT — warn so the UUID error isn't a mystery.
    try:
        UUID(tenant)
    except ValueError:
        print(
            f"⚠ Tenant {tenant!r} is not a UUID. If your DB's "
            f"site_health_samples.tenant_id is UUID (the SaaS schema), this "
            f"will fail — pass --tenant <uuid> or set MINIONS_FOUNDER_TENANT_ID "
            f"(it's in web/.env.local)."
        )

    apply_migrations()  # ensure site_health_samples + renewal_reminders exist

    now = datetime.now(tz=UTC)
    with connect() as conn, conn.cursor() as cur:
        _clear(cur, tenant)
        if args.clear_only:
            print(f"✓ Cleared demo Sentry rows for tenant {tenant!r}.")
            return 0

        sample_rows = _sample_rows(tenant, now)
        cur.executemany(
            """
            INSERT INTO site_health_samples
                (tenant_id, project, check_path, ts, ok, status_code,
                 latency_ms, error, cert_expires_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            sample_rows,
        )
        renewal_rows = _renewal_rows(tenant, now.date())
        cur.executemany(
            """
            INSERT INTO renewal_reminders
                (tenant_id, project, kind, name, due, url, note, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (tenant_id, project, kind, name) DO UPDATE
              SET due = EXCLUDED.due, url = EXCLUDED.url,
                  note = EXCLUDED.note, updated_at = NOW()
            """,
            renewal_rows,
        )

    print(
        f"✓ Seeded {len(sample_rows)} health samples across {len(_PROJECTS)} projects "
        f"+ {len(_RENEWALS)} renewals for tenant {tenant!r}.\n"
        f"  Open the console → Sentry to see 24h uptime/latency, a cert expiring in "
        f"4 days (Cinder), and an overdue domain renewal."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
