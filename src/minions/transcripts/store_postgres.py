"""Postgres-backed crew transcript store (mirrors ``dossiers/store_postgres.py``)."""

from __future__ import annotations

import json

from psycopg.types.json import Jsonb

from minions.db.connection import connect
from minions.models.transcript import CrewTranscriptMessage


class PostgresTranscriptStore:
    """Backed by the ``crew_transcripts`` table (migration 0008)."""

    def save(self, msg: CrewTranscriptMessage) -> CrewTranscriptMessage:
        payload = json.loads(msg.model_dump_json())
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO crew_transcripts (
                    id, run_id, project, crew, agent_role,
                    sequence, role_in_conversation, created_at, payload
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    payload = EXCLUDED.payload
                """,
                (
                    str(msg.id), msg.run_id, msg.project, msg.crew,
                    msg.agent_role, msg.sequence,
                    msg.role_in_conversation, msg.created_at, Jsonb(payload),
                ),
            )
        return msg

    def list_all(self) -> list[CrewTranscriptMessage]:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT payload FROM crew_transcripts "
                "ORDER BY created_at DESC LIMIT 5000"
            )
            rows = cur.fetchall()
        return [
            CrewTranscriptMessage.model_validate(
                r[0] if isinstance(r[0], dict) else json.loads(r[0])
            )
            for r in rows
        ]

    def list_by_run(self, run_id: str) -> list[CrewTranscriptMessage]:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT payload FROM crew_transcripts WHERE run_id = %s "
                "ORDER BY sequence ASC",
                (run_id,),
            )
            rows = cur.fetchall()
        return [
            CrewTranscriptMessage.model_validate(
                r[0] if isinstance(r[0], dict) else json.loads(r[0])
            )
            for r in rows
        ]

    def list_for_project(
        self, project: str, *, limit: int = 50
    ) -> list[CrewTranscriptMessage]:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT payload FROM crew_transcripts WHERE project = %s "
                "ORDER BY created_at DESC LIMIT %s",
                (project, limit),
            )
            rows = cur.fetchall()
        return [
            CrewTranscriptMessage.model_validate(
                r[0] if isinstance(r[0], dict) else json.loads(r[0])
            )
            for r in rows
        ]
