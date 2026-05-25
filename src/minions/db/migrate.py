"""Lightweight forward-only SQL migration runner.

Reads ``*.sql`` files from the bundled ``migrations/`` directory in
filename order, applies any not yet recorded in ``schema_migrations``,
records each as it goes. No down-migrations on purpose — Neon branches
are the rollback story.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from psycopg import Connection

from minions.db.connection import connect

_BOOTSTRAP_SCHEMA_MIGRATIONS = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""


def _migration_files() -> list[tuple[str, str]]:
    """Return [(filename, sql)] sorted by filename, packaged with the wheel."""
    pkg = files("minions.db") / "migrations"
    out: list[tuple[str, str]] = []
    for entry in sorted(pkg.iterdir(), key=lambda p: p.name):
        if entry.name.endswith(".sql"):
            out.append((entry.name, entry.read_text()))
    return out


def applied_migrations(conn: "Connection") -> set[str]:
    with conn.cursor() as cur:
        cur.execute(_BOOTSTRAP_SCHEMA_MIGRATIONS)
        cur.execute("SELECT filename FROM schema_migrations")
        return {row[0] for row in cur.fetchall()}


def apply_migrations(conn: "Connection | None" = None) -> list[str]:
    """Apply all pending migrations. Returns names of files applied."""
    if conn is None:
        with connect() as c:
            return apply_migrations(c)

    applied = applied_migrations(conn)
    newly: list[str] = []
    for filename, sql in _migration_files():
        if filename in applied:
            continue
        with conn.cursor() as cur:
            cur.execute(sql)
            cur.execute(
                "INSERT INTO schema_migrations (filename) VALUES (%s)",
                (filename,),
            )
        conn.commit()
        newly.append(filename)
    return newly


def _local_migration_dir() -> Path:
    """Directory holding the migration files (for diagnostics)."""
    return Path(__file__).parent / "migrations"
