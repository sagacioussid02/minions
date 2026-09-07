"""load_tenant_manifests() — reads tenant_projects into tagged Manifests."""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any

import pytest

from minions.config.portfolio_per_tenant import load_tenant_manifests

VALID_MANIFEST = {
    "name": "Acme",
    "description": "A tenant project",
    "source": {"kind": "github", "repo": "acme/app", "default_branch": "main"},
    "weekly_budget_usd": 25.0,
    "monthly_budget_usd": 100.0,
    "owner": "acme@example.com",
}


class _FakeCursor:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def execute(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None


class _FakeConn:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self._rows)

    def __enter__(self) -> _FakeConn:
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None


def _patch_connect(monkeypatch: pytest.MonkeyPatch, rows: list[tuple[Any, ...]]) -> None:
    @contextmanager
    def _fake_connect():
        yield _FakeConn(rows)

    import minions.config.portfolio_per_tenant as mod

    monkeypatch.setattr(mod, "connect", _fake_connect)


def test_load_tenant_manifests_tags_tenant_id(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_connect(
        monkeypatch,
        [("11111111-1111-1111-1111-111111111111", "acme-app", json.dumps(VALID_MANIFEST))],
    )
    manifests = load_tenant_manifests()
    key = "11111111-1111-1111-1111-111111111111:acme-app"
    assert key in manifests
    assert manifests[key].tenant_id == "11111111-1111-1111-1111-111111111111"
    assert manifests[key].name == "Acme"


def test_load_tenant_manifests_keys_avoid_collisions(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two tenants with a same-named project don't clobber each other."""
    _patch_connect(
        monkeypatch,
        [
            ("11111111-1111-1111-1111-111111111111", "app", json.dumps(VALID_MANIFEST)),
            ("22222222-2222-2222-2222-222222222222", "app", json.dumps(VALID_MANIFEST)),
        ],
    )
    manifests = load_tenant_manifests()
    assert len(manifests) == 2
    assert "11111111-1111-1111-1111-111111111111:app" in manifests
    assert "22222222-2222-2222-2222-222222222222:app" in manifests


def test_load_tenant_manifests_skips_invalid_row(monkeypatch: pytest.MonkeyPatch) -> None:
    """One tenant's malformed manifest_json doesn't take down the whole sweep."""
    broken = {**VALID_MANIFEST}
    del broken["owner"]  # owner is required
    _patch_connect(
        monkeypatch,
        [
            ("11111111-1111-1111-1111-111111111111", "broken", json.dumps(broken)),
            ("22222222-2222-2222-2222-222222222222", "ok", json.dumps(VALID_MANIFEST)),
        ],
    )
    manifests = load_tenant_manifests()
    assert "11111111-1111-1111-1111-111111111111:broken" not in manifests
    assert "22222222-2222-2222-2222-222222222222:ok" in manifests


def test_load_tenant_manifests_skips_malformed_json_string(monkeypatch: pytest.MonkeyPatch) -> None:
    """A manifest_json value that isn't even valid JSON (not just an invalid
    Manifest) is skipped too, rather than raising and aborting the sweep."""
    _patch_connect(
        monkeypatch,
        [
            ("11111111-1111-1111-1111-111111111111", "corrupt", "{not valid json"),
            ("22222222-2222-2222-2222-222222222222", "ok", json.dumps(VALID_MANIFEST)),
        ],
    )
    manifests = load_tenant_manifests()
    assert "11111111-1111-1111-1111-111111111111:corrupt" not in manifests
    assert "22222222-2222-2222-2222-222222222222:ok" in manifests
