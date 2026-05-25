"""JSON-backed store for Agile ritual and PM answer records."""

from __future__ import annotations

import json
from contextlib import suppress
from pathlib import Path
from typing import Any
from uuid import UUID

from minions.models.agile import AgileRitualRecord, PMAnswerRecord


class AgileStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text('{"rituals": {}, "pm_answers": {}}')

    def _load(self) -> dict[str, dict[str, dict[str, Any]]]:
        text = self.path.read_text()
        if not text.strip():
            return {"rituals": {}, "pm_answers": {}}
        data = json.loads(text)
        return {
            "rituals": data.get("rituals", {}),
            "pm_answers": data.get("pm_answers", {}),
        }

    def _save(self, data: dict[str, dict[str, dict[str, Any]]]) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, default=str))
        tmp.replace(self.path)

    def save_ritual(self, record: AgileRitualRecord) -> AgileRitualRecord:
        data = self._load()
        data["rituals"][str(record.id)] = record.model_dump(mode="json")
        self._save(data)
        with suppress(Exception):
            from minions.learning.capture import capture_ritual
            from minions.learning.store import AgentLearningStore

            capture_ritual(record, AgentLearningStore(self.path.parent / "agent_learning.json"))
        return record

    def list_rituals(self, project: str | None = None) -> list[AgileRitualRecord]:
        records = [
            AgileRitualRecord.model_validate(raw)
            for raw in self._load()["rituals"].values()
        ]
        if project is not None:
            records = [r for r in records if r.project == project]
        return sorted(records, key=lambda r: r.created_at, reverse=True)

    def get_ritual(self, record_id: UUID | str) -> AgileRitualRecord | None:
        raw = self._load()["rituals"].get(str(record_id))
        return AgileRitualRecord.model_validate(raw) if raw is not None else None

    def save_pm_answer(self, record: PMAnswerRecord) -> PMAnswerRecord:
        data = self._load()
        data["pm_answers"][str(record.id)] = record.model_dump(mode="json")
        self._save(data)
        with suppress(Exception):
            from minions.learning.capture import capture_pm_answer
            from minions.learning.store import AgentLearningStore

            capture_pm_answer(record, AgentLearningStore(self.path.parent / "agent_learning.json"))
        return record

    def list_pm_answers(self, project: str | None = None) -> list[PMAnswerRecord]:
        records = [
            PMAnswerRecord.model_validate(raw)
            for raw in self._load()["pm_answers"].values()
        ]
        if project is not None:
            records = [r for r in records if r.project == project]
        return sorted(records, key=lambda r: r.created_at, reverse=True)
