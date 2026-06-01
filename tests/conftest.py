"""Test-wide fixtures."""

from __future__ import annotations

import pytest

from minions import secrets


@pytest.fixture(autouse=True)
def _reset_secrets_resolver() -> None:
    """Reset the module-level secrets resolver before and after every test.

    Without this, the in-process cache leaks between tests (a value cached in
    test A would still be returned in test B even after monkeypatch clears the
    underlying env var).
    """
    secrets.reset()
    yield
    secrets.reset()


@pytest.fixture(autouse=True)
def _isolate_persistence_from_prod(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    """Pin every dual-backend store to JSON for the duration of a test.

    Without this, a developer .env that exports ``MINIONS_DATABASE_URL`` (the
    same one the live CLI uses) makes every store-factory call inside a test
    write to **production Postgres** — e.g. ``record_crew_summary`` inside
    ``audit_pr`` happily seeded 36 ``project='p'`` rows into the live
    ``crew_transcripts`` table before this fixture existed. The factories
    consult ``MINIONS_STORE_BACKEND`` / ``MINIONS_LOGS_BACKEND`` first and only
    fall back to ``has_database_url()`` when neither is set; pinning both to
    ``json`` AND removing ``MINIONS_DATABASE_URL`` closes both paths.

    Tests that intentionally exercise the Postgres round-trip live in
    ``tests/test_db_factory.py`` — they require ``MINIONS_DATABASE_URL`` to
    be present in the host env and would otherwise skip. We opt that
    single module out of the pin; everything else stays JSON-only.
    """
    if request.node.fspath.basename == "test_db_factory.py":
        return
    monkeypatch.setenv("MINIONS_STORE_BACKEND", "json")
    monkeypatch.setenv("MINIONS_LOGS_BACKEND", "json")
    monkeypatch.delenv("MINIONS_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
