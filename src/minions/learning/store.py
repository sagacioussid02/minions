"""JSON-backed store for durable agent learning records."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from minions.models.learning import AgentLearningRecord, LearningKind

_CONFIDENCE_RANK = {"high": 0, "medium": 1, "low": 2}


class AgentLearningStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text('{"records": {}}')

    def _load(self) -> dict[str, dict[str, dict[str, Any]]]:
        try:
            text = self.path.read_text()
            if not text.strip():
                return {"records": {}}
            data = json.loads(text)
        except (OSError, json.JSONDecodeError):
            return {"records": {}}
        return {"records": data.get("records", {})}

    def _save(self, data: dict[str, dict[str, dict[str, Any]]]) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, default=str))
        tmp.replace(self.path)

    def save(self, record: AgentLearningRecord) -> AgentLearningRecord:
        data = self._load()
        data["records"][str(record.id)] = record.model_dump(mode="json")
        self._save(data)
        return record

    def update(self, record: AgentLearningRecord) -> AgentLearningRecord:
        return self.save(record)

    def get(self, record_id: UUID | str) -> AgentLearningRecord | None:
        raw = self._load()["records"].get(str(record_id))
        return AgentLearningRecord.model_validate(raw) if raw is not None else None

    def list_all(self, include_inactive: bool = False) -> list[AgentLearningRecord]:
        records = [
            AgentLearningRecord.model_validate(raw) for raw in self._load()["records"].values()
        ]
        if not include_inactive:
            records = [record for record in records if _is_active(record)]
        return sorted(records, key=_sort_key)

    def list_by_agent(
        self,
        agent_id: str,
        include_inactive: bool = False,
    ) -> list[AgentLearningRecord]:
        return [
            record
            for record in self.list_all(include_inactive=include_inactive)
            if record.agent_id == agent_id
        ]

    def list_relevant(
        self,
        *,
        role: str | None = None,
        project: str | None = None,
        kind: LearningKind | None = None,
        limit: int = 10,
        include_global: bool = True,
    ) -> list[AgentLearningRecord]:
        records = self.list_all()
        if role is not None:
            records = [record for record in records if record.role == role]
        if project is not None:
            records = [
                record
                for record in records
                if record.project == project or (include_global and record.project is None)
            ]
        if kind is not None:
            records = [record for record in records if record.kind == kind]
        return records[:limit]

    def mark_used(self, record_id: UUID | str) -> AgentLearningRecord | None:
        record = self.get(record_id)
        if record is None:
            return None
        record.last_used_at = datetime.now(UTC)
        return self.update(record)

    def supersede(
        self,
        old_record_id: UUID | str,
        new_record_id: UUID | str,
    ) -> AgentLearningRecord | None:
        old_record = self.get(old_record_id)
        if old_record is None:
            return None
        old_record.superseded_by = UUID(str(new_record_id))
        return self.update(old_record)


def _is_active(record: AgentLearningRecord) -> bool:
    if record.superseded_by is not None:
        return False
    if record.expires_at is None:
        return True
    return record.expires_at > datetime.now(UTC)


def _sort_key(record: AgentLearningRecord) -> tuple[int, float]:
    return (_CONFIDENCE_RANK[record.confidence], -record.created_at.timestamp())
