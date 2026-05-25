"""Postgres (Neon) connection helper.

Resolves the connection string from the secrets backend chain — env var
``MINIONS_DATABASE_URL`` wins, then the standard ``DATABASE_URL`` env var,
then the secrets resolver under name ``database-url``. Raises
``SecretNotFound`` if none resolve, so callers can fall back to JSON.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from psycopg import Connection

from minions.secrets import SecretNotFound, get_secret


def get_database_url() -> str:
    """Return the Postgres URL or raise ``SecretNotFound``.

    Order: ``MINIONS_DATABASE_URL`` env → ``DATABASE_URL`` env → secrets
    backend ``database-url``.
    """
    direct = os.environ.get("MINIONS_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if direct:
        return direct
    return get_secret("database-url")


def has_database_url() -> bool:
    """True iff a Postgres URL resolves cleanly. Never raises."""
    try:
        get_database_url()
        return True
    except SecretNotFound:
        return False


@contextmanager
def connect() -> Iterator["Connection"]:
    """Open a Postgres connection using the resolved URL.

    Yields a ``psycopg.Connection`` with autocommit OFF so callers can
    wrap multi-statement work in transactions. Caller's ``with`` block
    commits on success / rolls back on exception via psycopg's protocol.
    """
    import psycopg

    url = get_database_url()
    with psycopg.connect(url) as conn:
        yield conn
