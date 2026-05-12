"""JSON-backed QuestionStore. Same pattern as DecisionStore."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

from minions.models.question import QuestionRecord, QuestionStatus


class QuestionStore:
    """JSON file at ``data/local/questions.json`` keyed by question id."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("{}")

    def _load_all(self) -> dict[str, dict]:
        text = self.path.read_text()
        if not text.strip():
            return {}
        return json.loads(text)

    def _save_all(self, data: dict[str, dict]) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, default=str))
        tmp.replace(self.path)

    def save(self, question: QuestionRecord) -> None:
        data = self._load_all()
        data[str(question.id)] = question.model_dump(mode="json")
        self._save_all(data)

    def get(self, question_id: UUID | str) -> QuestionRecord | None:
        raw = self._load_all().get(str(question_id))
        if raw is None:
            return None
        return QuestionRecord.model_validate(raw)

    def list_all(self) -> list[QuestionRecord]:
        return [QuestionRecord.model_validate(raw) for raw in self._load_all().values()]

    def list_by_status(self, status: QuestionStatus) -> list[QuestionRecord]:
        return [q for q in self.list_all() if q.status == status]
