"""Postgres (Neon) connection helper.

Resolves the connection string from the secrets backend chain — env var
``MINIONS_DATABASE_URL`` wins, then the standard ``DATABASE_URL`` env var,
then the secrets resolver under name ``database-url``. Raises
``SecretNotFound`` if none resolve, so callers can fall back to JSON.
"""

from __future__ import annotations

import contextvars
import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from psycopg import Connection

from minions.secrets import SecretNotFound, get_secret

# The tenant whose rows this process's writes should attribute to. ``None``
# (the default) means "founder" — every existing table's ``tenant_id`` column
# DEFAULT already falls back to the founder tenant when this GUC is unset, so
# founder-only code paths need no changes. Tenant-scoped cron/CLI entrypoints
# enter a scope once per manifest via ``tenant_scope()``.
_active_tenant: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "minions_active_tenant", default=None
)


@contextmanager
def tenant_scope(tenant_id: str) -> Iterator[None]:
    """Scope every ``connect()`` opened in this context to ``tenant_id``.

    Sets the ``app.tenant_id`` Postgres GUC (via ``SET LOCAL``, so it's
    transaction-scoped and never leaks across connections) on each new
    connection opened while active, so every INSERT that omits ``tenant_id``
    picks it up from the column DEFAULT instead of falling back to the
    founder tenant. Nestable; restores the previous scope on exit.
    """
    token = _active_tenant.set(str(tenant_id))
    try:
        yield
    finally:
        _active_tenant.reset(token)


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
def connect() -> Iterator[Connection]:
    """Open a Postgres connection using the resolved URL.

    Yields a ``psycopg.Connection`` with autocommit OFF so callers can
    wrap multi-statement work in transactions. Caller's ``with`` block
    commits on success / rolls back on exception via psycopg's protocol.
    """
    import psycopg

    url = get_database_url()
    with psycopg.connect(url) as conn:
        tenant_id = _active_tenant.get()
        if tenant_id is not None:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL app.tenant_id = %s", (tenant_id,))
        yield conn
