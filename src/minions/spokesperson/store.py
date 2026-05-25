"""JSON-backed store for spokesperson interview records."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

from minions.models.interview import (
    ConsultationRecord,
    InterviewMessageRecord,
    InterviewTaskProposal,
    InterviewThreadRecord,
)


class InterviewStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text(
                '{"threads": {}, "messages": {}, "consultations": {}, "tasks": {}}'
            )

    def _load(self) -> dict[str, dict[str, dict]]:
        text = self.path.read_text()
        if not text.strip():
            return {"threads": {}, "messages": {}, "consultations": {}, "tasks": {}}
        data = json.loads(text)
        return {
            "threads": data.get("threads", {}),
            "messages": data.get("messages", {}),
            "consultations": data.get("consultations", {}),
            "tasks": data.get("tasks", {}),
        }

    def _save(self, data: dict[str, dict[str, dict]]) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, default=str))
        tmp.replace(self.path)

    def save_thread(self, record: InterviewThreadRecord) -> InterviewThreadRecord:
        data = self._load()
        data["threads"][str(record.id)] = record.model_dump(mode="json")
        self._save(data)
        return record

    def get_thread(self, thread_id: UUID | str) -> InterviewThreadRecord | None:
        raw = self._load()["threads"].get(str(thread_id))
        return InterviewThreadRecord.model_validate(raw) if raw else None

    def list_threads(self, project: str | None = None) -> list[InterviewThreadRecord]:
        records = [
            InterviewThreadRecord.model_validate(raw)
            for raw in self._load()["threads"].values()
        ]
        if project is not None:
            records = [r for r in records if r.project == project]
        return sorted(records, key=lambda r: r.updated_at, reverse=True)

    def save_message(self, record: InterviewMessageRecord) -> InterviewMessageRecord:
        data = self._load()
        data["messages"][str(record.id)] = record.model_dump(mode="json")
        self._save(data)
        return record

    def list_messages(self, thread_id: UUID | str) -> list[InterviewMessageRecord]:
        records = [
            InterviewMessageRecord.model_validate(raw)
            for raw in self._load()["messages"].values()
            if raw.get("thread_id") == str(thread_id)
        ]
        return sorted(records, key=lambda r: r.created_at)

    def save_consultation(self, record: ConsultationRecord) -> ConsultationRecord:
        data = self._load()
        data["consultations"][str(record.id)] = record.model_dump(mode="json")
        self._save(data)
        return record

    def list_consultations(self, thread_id: UUID | str) -> list[ConsultationRecord]:
        records = [
            ConsultationRecord.model_validate(raw)
            for raw in self._load()["consultations"].values()
            if raw.get("thread_id") == str(thread_id)
        ]
        return sorted(records, key=lambda r: r.created_at)

    def save_task(self, record: InterviewTaskProposal) -> InterviewTaskProposal:
        data = self._load()
        data["tasks"][str(record.id)] = record.model_dump(mode="json")
        self._save(data)
        return record

    def list_tasks(self, thread_id: UUID | str | None = None) -> list[InterviewTaskProposal]:
        records = [
            InterviewTaskProposal.model_validate(raw)
            for raw in self._load()["tasks"].values()
        ]
        if thread_id is not None:
            records = [r for r in records if str(r.thread_id) == str(thread_id)]
        return sorted(records, key=lambda r: r.created_at, reverse=True)
