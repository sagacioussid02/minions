"""Tests for src/minions/dossiers/store.py (JSON backend)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from minions.dossiers import DossierDraftStore
from minions.models.dossier import DossierDraft, DossierSection, DossierStatus


def _make(
    project: str = "demo_five",
    status: DossierStatus = DossierStatus.DRAFTED,
    sha: str = "abc1234",
    generated_at: datetime | None = None,
) -> DossierDraft:
    return DossierDraft(
        project=project,
        commit_sha=sha,
        status=status,
        markdown="# Architecture\nSee `src/x.py:1` for entry.\n",
        sections_present=[DossierSection.ARCHITECTURE],
        generated_at=generated_at or datetime.now(UTC),
    )


def test_save_and_get(tmp_path: Path) -> None:
    store = DossierDraftStore(tmp_path / "dossier_drafts.json")
    d = _make()
    store.save(d)
    fetched = store.get(str(d.id))
    assert fetched is not None
    assert fetched.project == "demo_five"
    assert fetched.commit_sha == "abc1234"
    assert fetched.status is DossierStatus.DRAFTED
    assert DossierSection.ARCHITECTURE in fetched.sections_present


def test_list_for_project_filters_and_orders(tmp_path: Path) -> None:
    store = DossierDraftStore(tmp_path / "dossier_drafts.json")
    now = datetime.now(UTC)
    older = _make(sha="111", generated_at=now - timedelta(days=2))
    newer = _make(sha="222", generated_at=now)
    other = _make(project="demo_four", sha="999", generated_at=now)
    store.save(older)
    store.save(newer)
    store.save(other)

    rows = store.list_for_project("demo_five")
    assert [r.commit_sha for r in rows] == ["222", "111"]
    assert all(r.project == "demo_five" for r in rows)


def test_list_for_project_status_filter(tmp_path: Path) -> None:
    store = DossierDraftStore(tmp_path / "dossier_drafts.json")
    store.save(_make(status=DossierStatus.DRAFTED, sha="d1"))
    store.save(_make(status=DossierStatus.MERGED, sha="m1"))
    store.save(_make(status=DossierStatus.REJECTED, sha="r1"))

    merged = store.list_for_project("demo_five", status=DossierStatus.MERGED)
    assert [r.commit_sha for r in merged] == ["m1"]


def test_latest_merged(tmp_path: Path) -> None:
    store = DossierDraftStore(tmp_path / "dossier_drafts.json")
    now = datetime.now(UTC)
    store.save(_make(status=DossierStatus.MERGED, sha="old", generated_at=now - timedelta(days=5)))
    store.save(_make(status=DossierStatus.MERGED, sha="new", generated_at=now))
    store.save(
        _make(status=DossierStatus.DRAFTED, sha="draft", generated_at=now + timedelta(seconds=1))
    )

    latest = store.latest_merged("demo_five")
    assert latest is not None
    assert latest.commit_sha == "new"


def test_latest_merged_none_when_no_merged(tmp_path: Path) -> None:
    store = DossierDraftStore(tmp_path / "dossier_drafts.json")
    store.save(_make(status=DossierStatus.DRAFTED))
    assert store.latest_merged("demo_five") is None


def test_corrupt_file_returns_empty(tmp_path: Path) -> None:
    p = tmp_path / "dossier_drafts.json"
    p.write_text("{ not json")
    store = DossierDraftStore(p)
    assert store.list_all() == []
    store.save(_make())
    assert len(store.list_all()) == 1


def test_save_updates_existing(tmp_path: Path) -> None:
    store = DossierDraftStore(tmp_path / "dossier_drafts.json")
    d = _make()
    store.save(d)
    d.status = DossierStatus.PR_OPEN
    d.pr_url = "https://github.com/o/r/pull/42"
    d.pr_number = 42
    store.save(d)

    fetched = store.get(str(d.id))
    assert fetched is not None
    assert fetched.status is DossierStatus.PR_OPEN
    assert fetched.pr_number == 42
    assert len(store.list_all()) == 1
