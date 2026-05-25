"""JSON-backed deployment-verification store.

Mirrors the 3-file pattern used by ``dossiers/store.py``. Postgres
counterpart lives in ``store_postgres.py``; pick a backend via
``store_factory.make_deployment_store``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from minions.models.deployment import DeploymentRecord, DeploymentStatus


class DeploymentStore:
    """JSON file at ``data/local/deployments.json`` keyed by record id."""

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

    def save(self, record: DeploymentRecord) -> DeploymentRecord:
        all_data = self._load_all()
        all_data[str(record.id)] = json.loads(record.model_dump_json())
        self._save_all(all_data)
        return record

    def get(self, record_id: str) -> DeploymentRecord | None:
        raw = self._load_all().get(record_id)
        return DeploymentRecord.model_validate(raw) if raw else None

    def list_all(self) -> list[DeploymentRecord]:
        return [DeploymentRecord.model_validate(v) for v in self._load_all().values()]

    def find_by_sha(self, project: str, merge_sha: str) -> DeploymentRecord | None:
        for r in self.list_all():
            if r.project == project and r.merge_sha == merge_sha:
                return r
        return None

    def list_for_project(
        self,
        project: str,
        status: DeploymentStatus | None = None,
        limit: int = 100,
    ) -> list[DeploymentRecord]:
        rows = [r for r in self.list_all() if r.project == project]
        if status is not None:
            rows = [r for r in rows if r.status == status]
        rows.sort(key=lambda r: r.detected_at, reverse=True)
        return rows[:limit]
