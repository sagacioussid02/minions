"""SiteHealthStore backend selector. Same pattern as ``approval/store_factory``."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol

from minions.db.connection import has_database_url
from minions.sentry.site_health_store import AlertState, SiteHealthSample


class SiteHealthStoreLike(Protocol):
    def record(self, sample: SiteHealthSample) -> None: ...
    def list_recent_for_check(
        self, project: str, check_path: str, *, limit: int = 50
    ) -> list[SiteHealthSample]: ...
    def list_recent_for_project(
        self, project: str, *, limit: int = 500
    ) -> list[SiteHealthSample]: ...
    def list_all_samples(self) -> list[SiteHealthSample]: ...
    def get_alert_state(self, project: str, check_path: str) -> AlertState | None: ...
    def set_alert_state(self, state: AlertState) -> None: ...


def make_site_health_store(json_path: Path) -> SiteHealthStoreLike:
    """Pick a backend per env. ``json_path`` is the JSON fallback."""
    backend = (os.environ.get("MINIONS_STORE_BACKEND") or "").lower()
    if backend == "postgres":
        from minions.sentry.site_health_store_postgres import PostgresSiteHealthStore

        return PostgresSiteHealthStore()
    if backend == "json":
        from minions.sentry.site_health_store import SiteHealthStore

        return SiteHealthStore(json_path)
    if has_database_url():
        from minions.sentry.site_health_store_postgres import PostgresSiteHealthStore

        return PostgresSiteHealthStore()
    from minions.sentry.site_health_store import SiteHealthStore

    return SiteHealthStore(json_path)
