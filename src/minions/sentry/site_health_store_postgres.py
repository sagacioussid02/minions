"""Postgres-backed SiteHealthStore. Same interface as ``SiteHealthStore``."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from minions.db.connection import connect
from minions.sentry.site_health_store import AlertState, SiteHealthSample


class PostgresSiteHealthStore:
    """Backed by ``site_health_samples`` + ``site_alert_state``."""

    # ---- samples ----

    def record(self, sample: SiteHealthSample) -> None:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO site_health_samples (
                    project, check_path, ts, ok, status_code, latency_ms, error
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    sample.project,
                    sample.check_path,
                    sample.ts,
                    sample.ok,
                    sample.status_code,
                    sample.latency_ms,
                    sample.error,
                ),
            )

    def list_recent_for_check(
        self,
        project: str,
        check_path: str,
        *,
        limit: int = 50,
    ) -> list[SiteHealthSample]:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT project, check_path, ts, ok, status_code, latency_ms, error
                FROM site_health_samples
                WHERE project = %s AND check_path = %s
                ORDER BY ts DESC
                LIMIT %s
                """,
                (project, check_path, limit),
            )
            return [_row_to_sample(r) for r in cur.fetchall()]

    def list_recent_for_project(self, project: str, *, limit: int = 500) -> list[SiteHealthSample]:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT project, check_path, ts, ok, status_code, latency_ms, error
                FROM site_health_samples
                WHERE project = %s
                ORDER BY ts DESC
                LIMIT %s
                """,
                (project, limit),
            )
            return [_row_to_sample(r) for r in cur.fetchall()]

    def list_all_samples(self) -> list[SiteHealthSample]:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT project, check_path, ts, ok, status_code, latency_ms, error
                FROM site_health_samples
                ORDER BY ts DESC
                """
            )
            return [_row_to_sample(r) for r in cur.fetchall()]

    # ---- alert state ----

    def get_alert_state(self, project: str, check_path: str) -> AlertState | None:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT last_alert_at, last_alert_kind
                FROM site_alert_state
                WHERE project = %s AND check_path = %s
                """,
                (project, check_path),
            )
            row = cur.fetchone()
        if row is None:
            return None
        last_at, kind = row
        return AlertState(
            project=project,
            check_path=check_path,
            last_alert_at=last_at,
            last_alert_kind=kind,
        )

    def set_alert_state(self, state: AlertState) -> None:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO site_alert_state (
                    project, check_path, last_alert_at, last_alert_kind
                ) VALUES (%s, %s, %s, %s)
                ON CONFLICT (project, check_path) DO UPDATE SET
                    last_alert_at = EXCLUDED.last_alert_at,
                    last_alert_kind = EXCLUDED.last_alert_kind
                """,
                (
                    state.project,
                    state.check_path,
                    state.last_alert_at,
                    state.last_alert_kind,
                ),
            )


def _row_to_sample(row: tuple[Any, ...]) -> SiteHealthSample:
    project, check_path, ts, ok, status_code, latency_ms, error = row
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts)
    return SiteHealthSample(
        project=project,
        check_path=check_path,
        ts=ts,
        ok=bool(ok),
        status_code=status_code,
        latency_ms=latency_ms,
        error=error,
    )
