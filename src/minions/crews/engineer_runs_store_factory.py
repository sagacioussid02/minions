"""EngineerRunStore backend selector."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol

from minions.crews.engineer import EngineerResult
from minions.crews.engineer_runs_store import EngineerRunRecord
from minions.db.connection import has_database_url


class EngineerRunStoreLike(Protocol):
    def save(
        self, result: EngineerResult, *, project: str, tenant_id: str | None = None
    ) -> EngineerRunRecord: ...
    def get(self, decision_id: str) -> EngineerRunRecord | None: ...
    def list_all(self) -> list[EngineerRunRecord]: ...
    def list_by_project(self, project: str) -> list[EngineerRunRecord]: ...
    def update(self, record: EngineerRunRecord) -> EngineerRunRecord: ...


def make_engineer_runs_store(json_path: Path) -> EngineerRunStoreLike:
    backend = (os.environ.get("MINIONS_STORE_BACKEND") or "").lower()
    if backend == "postgres":
        from minions.crews.engineer_runs_store_postgres import PostgresEngineerRunStore

        return PostgresEngineerRunStore()
    if backend == "json":
        from minions.crews.engineer_runs_store import EngineerRunStore

        return EngineerRunStore(json_path)
    if has_database_url():
        from minions.crews.engineer_runs_store_postgres import PostgresEngineerRunStore

        return PostgresEngineerRunStore()
    from minions.crews.engineer_runs_store import EngineerRunStore

    return EngineerRunStore(json_path)
