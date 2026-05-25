"""Crew transcript store backend selector. Mirrors ``dossiers/store_factory.py``."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol

from minions.db.connection import has_database_url
from minions.models.transcript import CrewTranscriptMessage


class TranscriptStoreLike(Protocol):
    def save(self, msg: CrewTranscriptMessage) -> CrewTranscriptMessage: ...
    def list_all(self) -> list[CrewTranscriptMessage]: ...
    def list_by_run(self, run_id: str) -> list[CrewTranscriptMessage]: ...
    def list_for_project(self, project: str, *, limit: int = 50) -> list[CrewTranscriptMessage]: ...


def make_transcript_store(json_path: Path) -> TranscriptStoreLike:
    backend = (os.environ.get("MINIONS_STORE_BACKEND") or "").lower()
    if backend == "postgres":
        from minions.transcripts.store_postgres import PostgresTranscriptStore

        return PostgresTranscriptStore()
    if backend == "json":
        from minions.transcripts.store import TranscriptStore

        return TranscriptStore(json_path)
    if has_database_url():
        from minions.transcripts.store_postgres import PostgresTranscriptStore

        return PostgresTranscriptStore()
    from minions.transcripts.store import TranscriptStore

    return TranscriptStore(json_path)
