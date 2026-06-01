"""Postgres-backed AgentChatStore. Drop-in for ``AgentChatStore``."""

from __future__ import annotations

import json
from uuid import UUID

from psycopg.types.json import Jsonb

from minions.db.connection import connect
from minions.models.agent_chat import AgentChatMessage, AgentChatThread


class PostgresAgentChatStore:
    """Backed by ``agent_chat_threads`` and ``agent_chat_messages`` (migration 0010)."""

    # --- threads -------------------------------------------------------------

    def save_thread(self, thread: AgentChatThread) -> AgentChatThread:
        payload = json.loads(thread.model_dump_json())
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO agent_chat_threads (
                    id, agent_id, project, title, created_at, last_message_at, payload
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    agent_id = EXCLUDED.agent_id,
                    project = EXCLUDED.project,
                    title = EXCLUDED.title,
                    last_message_at = EXCLUDED.last_message_at,
                    payload = EXCLUDED.payload
                """,
                (
                    str(thread.id),
                    thread.agent_id,
                    thread.project,
                    thread.title,
                    thread.created_at,
                    thread.last_message_at,
                    Jsonb(payload),
                ),
            )
        return thread

    def get_thread(self, thread_id: UUID | str) -> AgentChatThread | None:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT payload FROM agent_chat_threads WHERE id = %s",
                (str(thread_id),),
            )
            row = cur.fetchone()
        if row is None:
            return None
        payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        return AgentChatThread.model_validate(payload)

    def list_threads_for_agent(self, agent_id: str) -> list[AgentChatThread]:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT payload FROM agent_chat_threads WHERE agent_id = %s "
                "ORDER BY last_message_at DESC",
                (agent_id,),
            )
            rows = cur.fetchall()
        return [
            AgentChatThread.model_validate(r[0] if isinstance(r[0], dict) else json.loads(r[0]))
            for r in rows
        ]

    # --- messages ------------------------------------------------------------

    def save_message(self, msg: AgentChatMessage) -> AgentChatMessage:
        payload = json.loads(msg.model_dump_json())
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO agent_chat_messages (
                    id, thread_id, role, content, created_at,
                    model, prompt_tokens, response_tokens, payload
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    content = EXCLUDED.content,
                    payload = EXCLUDED.payload
                """,
                (
                    str(msg.id),
                    str(msg.thread_id),
                    msg.role,
                    msg.content,
                    msg.created_at,
                    msg.model,
                    msg.prompt_tokens,
                    msg.response_tokens,
                    Jsonb(payload),
                ),
            )
            cur.execute(
                "UPDATE agent_chat_threads SET last_message_at = %s WHERE id = %s",
                (msg.created_at, str(msg.thread_id)),
            )
        return msg

    def list_messages(self, thread_id: UUID | str) -> list[AgentChatMessage]:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT payload FROM agent_chat_messages WHERE thread_id = %s "
                "ORDER BY created_at ASC",
                (str(thread_id),),
            )
            rows = cur.fetchall()
        return [
            AgentChatMessage.model_validate(r[0] if isinstance(r[0], dict) else json.loads(r[0]))
            for r in rows
        ]
