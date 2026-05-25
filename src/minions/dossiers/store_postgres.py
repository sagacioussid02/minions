"""Postgres-backed DossierDraftStore. Drop-in for ``DossierDraftStore``."""

from __future__ import annotations

import json

from psycopg.types.json import Jsonb

from minions.db.connection import connect
from minions.models.dossier import DossierDraft, DossierStatus


class PostgresDossierDraftStore:
    """Backed by the ``dossier_drafts`` table (migration 0007)."""

    def save(self, draft: DossierDraft) -> DossierDraft:
        payload = json.loads(draft.model_dump_json())
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO dossier_drafts (
                    id, project, commit_sha, status, generated_at,
                    pr_url, pr_number, merged_at, payload
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    project = EXCLUDED.project,
                    commit_sha = EXCLUDED.commit_sha,
                    status = EXCLUDED.status,
                    pr_url = EXCLUDED.pr_url,
                    pr_number = EXCLUDED.pr_number,
                    merged_at = EXCLUDED.merged_at,
                    payload = EXCLUDED.payload
                """,
                (
                    str(draft.id),
                    draft.project,
                    draft.commit_sha,
                    draft.status.value,
                    draft.generated_at,
                    draft.pr_url,
                    draft.pr_number,
                    draft.merged_at,
                    Jsonb(payload),
                ),
            )
        return draft

    def get(self, draft_id: str) -> DossierDraft | None:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT payload FROM dossier_drafts WHERE id = %s",
                (str(draft_id),),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return DossierDraft.model_validate(
            row[0] if isinstance(row[0], dict) else json.loads(row[0])
        )

    def list_all(self) -> list[DossierDraft]:
        with connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT payload FROM dossier_drafts ORDER BY generated_at DESC")
            rows = cur.fetchall()
        return [
            DossierDraft.model_validate(r[0] if isinstance(r[0], dict) else json.loads(r[0]))
            for r in rows
        ]

    def list_for_project(
        self, project: str, status: DossierStatus | None = None, limit: int = 100
    ) -> list[DossierDraft]:
        with connect() as conn, conn.cursor() as cur:
            if status is None:
                cur.execute(
                    "SELECT payload FROM dossier_drafts WHERE project = %s "
                    "ORDER BY generated_at DESC LIMIT %s",
                    (project, limit),
                )
            else:
                cur.execute(
                    "SELECT payload FROM dossier_drafts WHERE project = %s AND status = %s "
                    "ORDER BY generated_at DESC LIMIT %s",
                    (project, status.value, limit),
                )
            rows = cur.fetchall()
        return [
            DossierDraft.model_validate(r[0] if isinstance(r[0], dict) else json.loads(r[0]))
            for r in rows
        ]

    def latest_merged(self, project: str) -> DossierDraft | None:
        merged = self.list_for_project(project, status=DossierStatus.MERGED, limit=1)
        return merged[0] if merged else None
