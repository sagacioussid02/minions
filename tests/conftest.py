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
