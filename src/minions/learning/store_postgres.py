"""Postgres-backed store for durable agent learning records."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from minions.db.connection import connect
from minions.models.learning import AgentLearningRecord, LearningKind


class PostgresAgentLearningStore:
    def _ensure_table(self) -> None:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_learning (
                    id uuid PRIMARY KEY,
                    agent_id text NOT NULL,
                    role text NOT NULL,
                    project text,
                    kind text NOT NULL,
                    source_type text NOT NULL,
                    source_id text NOT NULL,
                    confidence text NOT NULL,
                    created_at timestamptz NOT NULL,
                    last_used_at timestamptz,
                    superseded_by uuid,
                    expires_at timestamptz,
                    payload jsonb NOT NULL
                )
                """
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS agent_learning_agent_idx "
                "ON agent_learning(agent_id)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS agent_learning_role_project_kind_idx "
                "ON agent_learning(role, project, kind)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS agent_learning_source_idx "
                "ON agent_learning(source_type, source_id)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS agent_learning_created_at_idx "
                "ON agent_learning(created_at DESC)"
            )

    def save(self, record: AgentLearningRecord) -> AgentLearningRecord:
        self._ensure_table()
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO agent_learning (
                    id, agent_id, role, project, kind, source_type, source_id,
                    confidence, created_at, last_used_at, superseded_by, expires_at, payload
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (id) DO UPDATE SET
                    agent_id = EXCLUDED.agent_id,
                    role = EXCLUDED.role,
                    project = EXCLUDED.project,
                    kind = EXCLUDED.kind,
                    source_type = EXCLUDED.source_type,
                    source_id = EXCLUDED.source_id,
                    confidence = EXCLUDED.confidence,
                    created_at = EXCLUDED.created_at,
                    last_used_at = EXCLUDED.last_used_at,
                    superseded_by = EXCLUDED.superseded_by,
                    expires_at = EXCLUDED.expires_at,
                    payload = EXCLUDED.payload
                """,
                (
                    str(record.id),
                    record.agent_id,
                    record.role,
                    record.project,
                    record.kind,
                    record.source_type,
                    record.source_id,
                    record.confidence,
                    record.created_at,
                    record.last_used_at,
                    str(record.superseded_by) if record.superseded_by else None,
                    record.expires_at,
                    Jsonb(record.model_dump(mode="json")),
                ),
            )
        return record

    def update(self, record: AgentLearningRecord) -> AgentLearningRecord:
        return self.save(record)

    def get(self, record_id: UUID | str) -> AgentLearningRecord | None:
        self._ensure_table()
        with connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT payload FROM agent_learning WHERE id = %s", (str(record_id),))
            row = cur.fetchone()
        if row is None:
            return None
        return _decode(row[0])

    def list_all(self, include_inactive: bool = False) -> list[AgentLearningRecord]:
        self._ensure_table()
        query = "SELECT payload FROM agent_learning"
        params: tuple[object, ...] = ()
        if not include_inactive:
            query += (
                " WHERE superseded_by IS NULL "
                "AND (expires_at IS NULL OR expires_at > NOW())"
            )
        query += (
            " ORDER BY CASE confidence "
            "WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, created_at DESC"
        )
        with connect() as conn, conn.cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()
        return [_decode(row[0]) for row in rows]

    def list_by_agent(
        self,
        agent_id: str,
        include_inactive: bool = False,
    ) -> list[AgentLearningRecord]:
        self._ensure_table()
        query = "SELECT payload FROM agent_learning WHERE agent_id = %s"
        params: list[object] = [agent_id]
        if not include_inactive:
            query += " AND superseded_by IS NULL AND (expires_at IS NULL OR expires_at > NOW())"
        query += (
            " ORDER BY CASE confidence "
            "WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, created_at DESC"
        )
        with connect() as conn, conn.cursor() as cur:
            cur.execute(query, tuple(params))
            rows = cur.fetchall()
        return [_decode(row[0]) for row in rows]

    def list_relevant(
        self,
        *,
        role: str | None = None,
        project: str | None = None,
        kind: LearningKind | None = None,
        limit: int = 10,
        include_global: bool = True,
    ) -> list[AgentLearningRecord]:
        self._ensure_table()
        filters = ["superseded_by IS NULL", "(expires_at IS NULL OR expires_at > NOW())"]
        params: list[object] = []
        if role is not None:
            filters.append("role = %s")
            params.append(role)
        if project is not None:
            if include_global:
                filters.append("(project = %s OR project IS NULL)")
            else:
                filters.append("project = %s")
            params.append(project)
        if kind is not None:
            filters.append("kind = %s")
            params.append(kind)
        params.append(limit)
        query = (
            "SELECT payload FROM agent_learning WHERE "
            + " AND ".join(filters)
            + " ORDER BY CASE confidence "
            "WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, created_at DESC "
            "LIMIT %s"
        )
        with connect() as conn, conn.cursor() as cur:
            cur.execute(query, tuple(params))
            rows = cur.fetchall()
        return [_decode(row[0]) for row in rows]

    def mark_used(self, record_id: UUID | str) -> AgentLearningRecord | None:
        self._ensure_table()
        used_at = datetime.now(UTC)
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT payload FROM agent_learning WHERE id = %s",
                (str(record_id),),
            )
            row = cur.fetchone()
            if row is None:
                return None
            record = _decode(row[0])
            record.last_used_at = used_at
            cur.execute(
                """
                UPDATE agent_learning
                SET last_used_at = %s, payload = %s
                WHERE id = %s
                """,
                (used_at, Jsonb(record.model_dump(mode="json")), str(record.id)),
            )
        return record

    def supersede(
        self,
        old_record_id: UUID | str,
        new_record_id: UUID | str,
    ) -> AgentLearningRecord | None:
        self._ensure_table()
        superseded_by = UUID(str(new_record_id))
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT payload FROM agent_learning WHERE id = %s",
                (str(old_record_id),),
            )
            row = cur.fetchone()
            if row is None:
                return None
            record = _decode(row[0])
            record.superseded_by = superseded_by
            cur.execute(
                """
                UPDATE agent_learning
                SET superseded_by = %s, payload = %s
                WHERE id = %s
                """,
                (
                    str(superseded_by),
                    Jsonb(record.model_dump(mode="json")),
                    str(record.id),
                ),
            )
        return record


def _decode(raw: object) -> AgentLearningRecord:
    if isinstance(raw, dict):
        payload: dict[str, Any] = raw
    elif isinstance(raw, str | bytes | bytearray):
        payload = json.loads(raw)
    else:
        raise TypeError(f"Unsupported agent learning payload type: {type(raw).__name__}")
    return AgentLearningRecord.model_validate(payload)
