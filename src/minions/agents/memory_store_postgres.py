"""Postgres-backed agent memory store."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from minions.db.connection import connect
from minions.models.agent_memory import AgentMemoryRecord


class PostgresAgentMemoryStore:
    def save(self, record: AgentMemoryRecord) -> AgentMemoryRecord:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO agent_memory (
                    id, agent_id, sprint_number, decision_id, task_id, pr_url,
                    event, summary, details, created_at, tier
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (id) DO UPDATE SET
                    agent_id = EXCLUDED.agent_id,
                    sprint_number = EXCLUDED.sprint_number,
                    decision_id = EXCLUDED.decision_id,
                    task_id = EXCLUDED.task_id,
                    pr_url = EXCLUDED.pr_url,
                    event = EXCLUDED.event,
                    summary = EXCLUDED.summary,
                    details = EXCLUDED.details,
                    created_at = EXCLUDED.created_at,
                    tier = EXCLUDED.tier
                """,
                (
                    str(record.id),
                    record.agent_id,
                    record.sprint_number,
                    str(record.decision_id) if record.decision_id else None,
                    str(record.task_id) if record.task_id else None,
                    record.pr_url,
                    record.event,
                    record.summary,
                    record.details,
                    record.created_at,
                    record.tier,
                ),
            )
        return record

    def record(self, **kwargs: Any) -> AgentMemoryRecord:
        return self.save(AgentMemoryRecord(**kwargs))

    def get(self, record_id: UUID | str) -> AgentMemoryRecord | None:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(_SELECT + " WHERE id = %s::uuid", (str(record_id),))
            row = cur.fetchone()
        return _row_to_record(row) if row else None

    def list_all(self) -> list[AgentMemoryRecord]:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(_SELECT + " ORDER BY created_at DESC")
            rows = cur.fetchall()
        return [_row_to_record(row) for row in rows]

    def list_by_agent(
        self,
        agent_id: str,
        *,
        include_cold: bool = False,
    ) -> list[AgentMemoryRecord]:
        with connect() as conn, conn.cursor() as cur:
            if include_cold:
                cur.execute(_SELECT + " WHERE agent_id = %s ORDER BY created_at DESC", (agent_id,))
            else:
                cur.execute(
                    _SELECT + " WHERE agent_id = %s AND tier = 'hot' ORDER BY created_at DESC",
                    (agent_id,),
                )
            rows = cur.fetchall()
        return [_row_to_record(row) for row in rows]

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
        changed = 0
        with connect() as conn, conn.cursor() as cur:
            for project, current in current_by_project.items():
                cur.execute(
                    """
                    UPDATE agent_memory
                    SET tier = 'cold'
                    WHERE tier = 'hot'
                      AND sprint_number IS NOT NULL
                      AND sprint_number < %s
                      AND split_part(split_part(agent_id, '@', 2), '#', 1) = %s
                    """,
                    (current - 1, project),
                )
                changed += cur.rowcount or 0
        return changed


_SELECT = (
    "SELECT id::text, agent_id, sprint_number, decision_id::text, task_id::text, "
    "pr_url, event, summary, details, created_at, tier FROM agent_memory"
)


def _row_to_record(row: tuple) -> AgentMemoryRecord:
    (
        id_,
        agent_id,
        sprint_number,
        decision_id,
        task_id,
        pr_url,
        event,
        summary,
        details,
        created_at,
        tier,
    ) = row
    return AgentMemoryRecord.model_validate(
        {
            "id": id_,
            "agent_id": agent_id,
            "sprint_number": sprint_number,
            "decision_id": decision_id,
            "task_id": task_id,
            "pr_url": pr_url,
            "event": event,
            "summary": summary,
            "details": details,
            "created_at": created_at,
            "tier": tier,
        }
    )
