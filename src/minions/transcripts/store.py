"""JSON-backed crew transcript store.

Mirrors the 3-file pattern used by ``dossiers/store.py`` and the other
domain stores. Postgres counterpart lives in ``store_postgres.py``;
pick a backend via ``store_factory.make_transcript_store``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from minions.models.transcript import CrewTranscriptMessage


class TranscriptStore:
    """JSON file at ``data/local/crew_transcripts.json`` keyed by id."""

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

    def save(self, msg: CrewTranscriptMessage) -> CrewTranscriptMessage:
        all_data = self._load_all()
        all_data[str(msg.id)] = json.loads(msg.model_dump_json())
        self._save_all(all_data)
        return msg

    def list_all(self) -> list[CrewTranscriptMessage]:
        return [
            CrewTranscriptMessage.model_validate(v)
            for v in self._load_all().values()
        ]

    def list_by_run(self, run_id: str) -> list[CrewTranscriptMessage]:
        rows = [m for m in self.list_all() if m.run_id == run_id]
        rows.sort(key=lambda m: m.sequence)
        return rows

    def list_for_project(
        self, project: str, *, limit: int = 50
    ) -> list[CrewTranscriptMessage]:
        rows = [m for m in self.list_all() if m.project == project]
        rows.sort(key=lambda m: m.created_at, reverse=True)
        return rows[:limit]
