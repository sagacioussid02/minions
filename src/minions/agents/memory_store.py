"""JSON-backed hot/cold memory store for named agents."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID

from minions.models.agent_memory import AgentMemoryRecord


class AgentMemoryStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            return {}
        if isinstance(data, dict) and "records" in data:
            records = data.get("records")
            return records if isinstance(records, dict) else {}
        return data if isinstance(data, dict) else {}

    def _save(self, data: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps({"records": data}, indent=2, default=str))
        tmp.replace(self.path)

    def save(self, record: AgentMemoryRecord) -> AgentMemoryRecord:
        data = self._load()
        data[str(record.id)] = record.model_dump(mode="json")
        self._save(data)
        return record

    def record(self, **kwargs: Any) -> AgentMemoryRecord:
        return self.save(AgentMemoryRecord(**kwargs))

    def get(self, record_id: UUID | str) -> AgentMemoryRecord | None:
        raw = self._load().get(str(record_id))
        return AgentMemoryRecord.model_validate(raw) if raw is not None else None

    def list_all(self) -> list[AgentMemoryRecord]:
        return sorted(
            (AgentMemoryRecord.model_validate(raw) for raw in self._load().values()),
            key=lambda record: record.created_at,
            reverse=True,
        )

    def list_by_agent(
        self,
        agent_id: str,
        *,
        include_cold: bool = False,
    ) -> list[AgentMemoryRecord]:
        return [
            record
            for record in self.list_all()
            if record.agent_id == agent_id and (include_cold or record.tier == "hot")
        ]

    def list_hot(self, agent_id: str, *, char_cap: int = 5000) -> list[AgentMemoryRecord]:
        out: list[AgentMemoryRecord] = []
        total = 0
        for record in self.list_by_agent(agent_id):
            size = len(record.summary) + len(record.details or "")
            if out and total + size > char_cap:
                break
            if size > char_cap and not out:
                record = record.model_copy(update={"summary": record.summary[:char_cap].rstrip()})
                size = len(record.summary)
            out.append(record)
            total += size
        return out

    def demote_hot_older_than(self, current_by_project: dict[str, int]) -> int:
        data = self._load()
        changed = 0
        for record_id, raw in list(data.items()):
            record = AgentMemoryRecord.model_validate(raw)
            project = _project_from_agent_id(record.agent_id)
            current = current_by_project.get(project or "")
            if (
                record.tier == "hot"
                and current is not None
                and record.sprint_number is not None
                and record.sprint_number < current - 1
            ):
                record.tier = "cold"
                data[record_id] = record.model_dump(mode="json")
                changed += 1
        if changed:
            self._save(data)
        return changed


def _project_from_agent_id(agent_id: str) -> str | None:
    if "@" not in agent_id:
        return None
    return agent_id.rsplit("@", 1)[1].split("#", 1)[0]
