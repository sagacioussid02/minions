"""Postgres-backed QuestionStore. Same interface as ``QuestionStore``."""

from __future__ import annotations

import json
from uuid import UUID

from psycopg.types.json import Jsonb

from minions.db.connection import connect
from minions.models.question import QuestionRecord, QuestionStatus


class PostgresQuestionStore:
    """Backed by the ``questions`` table. Drop-in for ``QuestionStore``."""

    def save(self, question: QuestionRecord) -> None:
        payload = question.model_dump(mode="json")
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO questions (
                    id, project, status, target_role, created_at, payload
                ) VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    project = EXCLUDED.project,
                    status = EXCLUDED.status,
                    target_role = EXCLUDED.target_role,
                    payload = EXCLUDED.payload
                """,
                (
                    str(question.id),
                    question.project,
                    question.status.value,
                    question.target_role,
                    question.created_at,
                    Jsonb(payload),
                ),
            )

    def get(self, question_id: UUID | str) -> QuestionRecord | None:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT payload FROM questions WHERE id = %s",
                (str(question_id),),
            )
            row = cur.fetchone()
        if row is None:
            return None
        payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        return QuestionRecord.model_validate(payload)

    def list_all(self) -> list[QuestionRecord]:
        with connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT payload FROM questions ORDER BY created_at DESC")
            rows = cur.fetchall()
        return [
            QuestionRecord.model_validate(r[0] if isinstance(r[0], dict) else json.loads(r[0]))
            for r in rows
        ]

    def list_by_status(self, status: QuestionStatus) -> list[QuestionRecord]:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT payload FROM questions WHERE status = %s ORDER BY created_at DESC",
                (status.value,),
            )
            rows = cur.fetchall()
        return [
            QuestionRecord.model_validate(r[0] if isinstance(r[0], dict) else json.loads(r[0]))
            for r in rows
        ]
