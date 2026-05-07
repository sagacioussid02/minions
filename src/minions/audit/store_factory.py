"""AuditFindingStore backend selector. Mirrors approval/store_factory.py."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol

from minions.db.connection import has_database_url
from minions.models.audit import AuditFinding


class AuditFindingStoreLike(Protocol):
    def save(self, finding: AuditFinding) -> AuditFinding: ...
    def get(self, finding_id: str) -> AuditFinding | None: ...
    def list_all(self) -> list[AuditFinding]: ...
    def list_open(self) -> list[AuditFinding]: ...
    def list_by_pr_url(self, pr_url: str) -> list[AuditFinding]: ...
    def has_finding_for_pr(self, pr_url: str) -> bool: ...


def make_audit_findings_store(json_path: Path) -> AuditFindingStoreLike:
    backend = (os.environ.get("MINIONS_STORE_BACKEND") or "").lower()
    if backend == "postgres":
        from minions.audit.store_postgres import PostgresAuditFindingStore

        return PostgresAuditFindingStore()
    if backend == "json":
        from minions.audit.store import AuditFindingStore

        return AuditFindingStore(json_path)
    if has_database_url():
        from minions.audit.store_postgres import PostgresAuditFindingStore

        return PostgresAuditFindingStore()
    from minions.audit.store import AuditFindingStore

    return AuditFindingStore(json_path)
