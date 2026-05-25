"""DossierDraftStore backend selector. Mirrors ``audit/store_factory.py``."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol

from minions.db.connection import has_database_url
from minions.models.dossier import DossierDraft, DossierStatus


class DossierStoreLike(Protocol):
    def save(self, draft: DossierDraft) -> DossierDraft: ...
    def get(self, draft_id: str) -> DossierDraft | None: ...
    def list_all(self) -> list[DossierDraft]: ...
    def list_for_project(
        self, project: str, status: DossierStatus | None = None, limit: int = 100
    ) -> list[DossierDraft]: ...
    def latest_merged(self, project: str) -> DossierDraft | None: ...


def make_dossier_store(json_path: Path) -> DossierStoreLike:
    backend = (os.environ.get("MINIONS_STORE_BACKEND") or "").lower()
    if backend == "postgres":
        from minions.dossiers.store_postgres import PostgresDossierDraftStore

        return PostgresDossierDraftStore()
    if backend == "json":
        from minions.dossiers.store import DossierDraftStore

        return DossierDraftStore(json_path)
    if has_database_url():
        from minions.dossiers.store_postgres import PostgresDossierDraftStore

        return PostgresDossierDraftStore()
    from minions.dossiers.store import DossierDraftStore

    return DossierDraftStore(json_path)
