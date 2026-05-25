"""Persistence for ``DossierDraft`` records.

JSON file at ``data/local/dossier_drafts.json`` keyed by draft id. The Postgres
counterpart lives in ``store_postgres.py``; pick a backend via
``store_factory.make_dossier_store``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from minions.models.dossier import DossierDraft, DossierStatus


class DossierDraftStore:
    """JSON-file store for DossierDraft records."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _load_all(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save_all(self, data: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2, default=str))

    def save(self, draft: DossierDraft) -> DossierDraft:
        all_data = self._load_all()
        all_data[str(draft.id)] = json.loads(draft.model_dump_json())
        self._save_all(all_data)
        return draft

    def get(self, draft_id: str) -> DossierDraft | None:
        raw = self._load_all().get(draft_id)
        return DossierDraft.model_validate(raw) if raw else None

    def list_all(self) -> list[DossierDraft]:
        return [DossierDraft.model_validate(v) for v in self._load_all().values()]

    def list_for_project(
        self, project: str, status: DossierStatus | None = None, limit: int = 100
    ) -> list[DossierDraft]:
        rows = [d for d in self.list_all() if d.project == project]
        if status is not None:
            rows = [d for d in rows if d.status == status]
        rows.sort(key=lambda d: d.generated_at, reverse=True)
        return rows[:limit]

    def latest_merged(self, project: str) -> DossierDraft | None:
        merged = self.list_for_project(project, status=DossierStatus.MERGED, limit=1)
        return merged[0] if merged else None
