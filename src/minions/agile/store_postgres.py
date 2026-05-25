"""Postgres-backed Agile store."""

from __future__ import annotations

import json
from contextlib import suppress
from uuid import UUID

from psycopg.types.json import Jsonb

from minions.db.connection import connect
from minions.models.agile import AgileRitualRecord, PMAnswerRecord


class PostgresAgileStore:
    def _ensure_tables(self) -> None:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS agile_rituals (
                    id uuid PRIMARY KEY,
                    project text NOT NULL,
                    ritual text NOT NULL,
                    period_start timestamptz NOT NULL,
                    period_end timestamptz NOT NULL,
                    created_at timestamptz NOT NULL,
                    payload jsonb NOT NULL
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS pm_answers (
                    id uuid PRIMARY KEY,
                    project text NOT NULL,
                    created_at timestamptz NOT NULL,
                    payload jsonb NOT NULL
                )
                """
            )

    def save_ritual(self, record: AgileRitualRecord) -> AgileRitualRecord:
        self._ensure_tables()
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO agile_rituals (
                    id, project, ritual, period_start, period_end, created_at, payload
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    project = EXCLUDED.project,
                    ritual = EXCLUDED.ritual,
                    period_start = EXCLUDED.period_start,
                    period_end = EXCLUDED.period_end,
                    created_at = EXCLUDED.created_at,
                    payload = EXCLUDED.payload
                """,
                (
                    str(record.id),
                    record.project,
                    record.ritual,
                    record.period_start,
                    record.period_end,
                    record.created_at,
                    Jsonb(record.model_dump(mode="json")),
                ),
            )
        with suppress(Exception):
            from minions.learning.capture import capture_ritual
            from minions.learning.store_postgres import PostgresAgentLearningStore

            capture_ritual(record, PostgresAgentLearningStore())
        return record

    def list_rituals(self, project: str | None = None) -> list[AgileRitualRecord]:
        self._ensure_tables()
        with connect() as conn, conn.cursor() as cur:
            if project is None:
                cur.execute("SELECT payload FROM agile_rituals ORDER BY created_at DESC")
            else:
                cur.execute(
                    "SELECT payload FROM agile_rituals WHERE project = %s "
                    "ORDER BY created_at DESC",
                    (project,),
                )
            rows = cur.fetchall()
        return [
            AgileRitualRecord.model_validate(
                row[0] if isinstance(row[0], dict) else json.loads(row[0])
            )
            for row in rows
        ]

    def get_ritual(self, record_id: UUID | str) -> AgileRitualRecord | None:
        self._ensure_tables()
        with connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT payload FROM agile_rituals WHERE id = %s", (str(record_id),))
            row = cur.fetchone()
        if row is None:
            return None
        return AgileRitualRecord.model_validate(
            row[0] if isinstance(row[0], dict) else json.loads(row[0])
        )

    def save_pm_answer(self, record: PMAnswerRecord) -> PMAnswerRecord:
        self._ensure_tables()
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO pm_answers (id, project, created_at, payload)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    project = EXCLUDED.project,
                    created_at = EXCLUDED.created_at,
                    payload = EXCLUDED.payload
                """,
                (
                    str(record.id),
                    record.project,
                    record.created_at,
                    Jsonb(record.model_dump(mode="json")),
                ),
            )
        with suppress(Exception):
            from minions.learning.capture import capture_pm_answer
            from minions.learning.store_postgres import PostgresAgentLearningStore

            capture_pm_answer(record, PostgresAgentLearningStore())
        return record

    def list_pm_answers(self, project: str | None = None) -> list[PMAnswerRecord]:
        self._ensure_tables()
        with connect() as conn, conn.cursor() as cur:
            if project is None:
                cur.execute("SELECT payload FROM pm_answers ORDER BY created_at DESC")
            else:
                cur.execute(
                    "SELECT payload FROM pm_answers WHERE project = %s "
                    "ORDER BY created_at DESC",
                    (project,),
                )
            rows = cur.fetchall()
        return [
            PMAnswerRecord.model_validate(
                row[0] if isinstance(row[0], dict) else json.loads(row[0])
            )
            for row in rows
        ]
