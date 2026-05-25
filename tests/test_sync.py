"""Tests for src/minions/sync.py — PR merge state sync."""

from __future__ import annotations

import json as json_lib
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

import httpx
import pytest

from minions.crews.engineer import EngineerResult
from minions.crews.engineer_runs_store import EngineerRunStore
from minions.github.client import GitHubClient
from minions.models.manifest import Manifest
from minions.sync import _pr_state_from_response, sync_pr_status, sync_record


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> GitHubClient:
    return GitHubClient(token="x", repo="o/r", transport=httpx.MockTransport(handler))


def _make_manifest(name: str = "p", repo: str = "o/r") -> Manifest:
    return Manifest.model_validate(
        {
            "name": name,
            "description": "test",
            "source": {"kind": "github", "path": "/tmp", "repo": repo, "default_branch": "main"},
            "weekly_budget_usd": 1.0,
            "monthly_budget_usd": 4.0,
            "owner": "o@o",
        }
    )


def _saved_record(store: EngineerRunStore, **overrides):
    """Save a baseline EngineerResult through the store and return the record."""
    base = {
        "decision_id": "dec-1",
        "pr_url": "https://github.com/o/r/pull/1",
        "pr_number": 1,
        "branch_name": "minions/eng/x",
        "files_changed": ["a.py"],
        "files_rejected": [],
        "operator_comment_posted": True,
        "dry_run": False,
    }
    base.update(overrides)
    result = EngineerResult(**base)
    return store.save(result, project="p")


# ---- _pr_state_from_response -----------------------------------------------


def test_state_resolves_merged() -> None:
    assert _pr_state_from_response(merged=True, state="closed") == "merged"


def test_state_resolves_closed_without_merge() -> None:
    assert _pr_state_from_response(merged=False, state="closed") == "closed"


def test_state_resolves_open() -> None:
    assert _pr_state_from_response(merged=False, state="open") == "open"


# ---- sync_record -----------------------------------------------------------


def test_sync_record_marks_merged_and_writes_back(tmp_path: Path) -> None:
    store = EngineerRunStore(tmp_path / "runs.json")
    rec = _saved_record(store)

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET" and req.url.path == "/repos/o/r/pulls/1":
            return httpx.Response(
                200,
                json={
                    "number": 1,
                    "title": "x", "body": "b", "state": "closed",
                    "head": {"ref": "minions/eng/x"}, "base": {"ref": "main"},
                    "draft": False, "html_url": "u",
                    "merged": True, "merged_at": "2026-05-04T22:50:00Z",
                    "closed_at": "2026-05-04T22:50:00Z",
                },
            )
        return httpx.Response(404)

    outcome = sync_record(rec, github=_client(handler), store=store)
    assert outcome.before is None
    assert outcome.after == "merged"
    assert outcome.changed
    assert outcome.error is None

    after = store.get("dec-1")
    assert after is not None
    assert after.pr_state == "merged"
    assert after.merged_at is not None
    assert after.last_synced_at is not None


def test_sync_record_open_pr_unchanged_state(tmp_path: Path) -> None:
    store = EngineerRunStore(tmp_path / "runs.json")
    rec = _saved_record(store)

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "number": 1, "title": "x", "body": "", "state": "open",
                "head": {"ref": "x"}, "base": {"ref": "main"}, "draft": True,
                "html_url": "u", "merged": False, "merged_at": None,
            },
        )

    outcome = sync_record(rec, github=_client(handler), store=store)
    assert outcome.after == "open"
    assert store.get("dec-1").pr_state == "open"  # type: ignore[union-attr]


def test_sync_record_closed_without_merge(tmp_path: Path) -> None:
    store = EngineerRunStore(tmp_path / "runs.json")
    rec = _saved_record(store)

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "number": 1, "title": "x", "body": "", "state": "closed",
                "head": {"ref": "x"}, "base": {"ref": "main"}, "draft": False,
                "html_url": "u", "merged": False, "merged_at": None,
                "closed_at": "2026-05-04T22:00:00Z",
            },
        )

    outcome = sync_record(rec, github=_client(handler), store=store)
    assert outcome.after == "closed"


def test_sync_record_handles_api_error(tmp_path: Path) -> None:
    store = EngineerRunStore(tmp_path / "runs.json")
    rec = _saved_record(store)

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"message": "down"})

    outcome = sync_record(rec, github=_client(handler), store=store)
    assert outcome.error is not None
    # Record should NOT be mutated on error.
    assert store.get("dec-1").pr_state is None  # type: ignore[union-attr]


def test_sync_record_no_pr_number(tmp_path: Path) -> None:
    store = EngineerRunStore(tmp_path / "runs.json")
    rec = _saved_record(store, pr_number=None)

    def handler(req: httpx.Request) -> httpx.Response:
        raise AssertionError("must not call API when pr_number is None")

    outcome = sync_record(rec, github=_client(handler), store=store)
    assert outcome.error == "no pr_number on record"


# ---- sync_pr_status (multi-record) -----------------------------------------


def test_sync_pr_status_skips_already_merged(tmp_path: Path) -> None:
    store = EngineerRunStore(tmp_path / "runs.json")
    rec = _saved_record(store)
    # Pretend a prior sync already marked it merged.
    store.update(rec.model_copy(update={"pr_state": "merged"}))

    def handler(req: httpx.Request) -> httpx.Response:
        raise AssertionError("merged records must not be re-fetched")

    report = sync_pr_status(
        store=store,
        open_github_client=lambda m: _client(handler),
        manifests={"p": _make_manifest()},
    )
    assert len(report.outcomes) == 0


def test_sync_pr_status_skips_when_client_unavailable(tmp_path: Path) -> None:
    """Local-only project / TBD repo → client factory returns None → skipped with error."""
    store = EngineerRunStore(tmp_path / "runs.json")
    _saved_record(store)
    report = sync_pr_status(
        store=store,
        open_github_client=lambda m: None,
        manifests={"p": _make_manifest()},
    )
    assert len(report.outcomes) == 1
    assert report.outcomes[0].error is not None
    assert "GitHub client" in report.outcomes[0].error


def test_sync_backfills_pre_b2_executed_decisions(tmp_path: Path) -> None:
    """Decisions implemented before EngineerRunStore landed can still be synced."""
    from minions.approval.store import DecisionStore
    from minions.models.decision import Decision, DecisionStatus, DecisionType

    decision_store = DecisionStore(tmp_path / "decisions.json")
    d = Decision(
        project="p",
        type=DecisionType.FEATURE,
        summary="x",
        rationale="r",
        diff_or_plan="p",
        risk="low",
        proposer_role="manager",
        proposer_agent_id="m@p",
        status=DecisionStatus.EXECUTED,
        pr_url="https://github.com/o/r/pull/42",
    )
    decision_store.save(d)

    runs_store = EngineerRunStore(tmp_path / "runs.json")
    assert runs_store.list_all() == []  # no record yet

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/repos/o/r/pulls/42":
            return httpx.Response(
                200,
                json={
                    "number": 42, "title": "x", "body": "", "state": "closed",
                    "head": {"ref": "x"}, "base": {"ref": "main"}, "draft": False,
                    "html_url": "u", "merged": True, "merged_at": "2026-05-05T10:00:00Z",
                },
            )
        return httpx.Response(404)

    report = sync_pr_status(
        store=runs_store,
        open_github_client=lambda m: _client(handler),
        manifests={"p": _make_manifest()},
        decision_store=decision_store,
    )
    # One outcome from the newly-backfilled record.
    assert len(report.outcomes) == 1
    assert report.outcomes[0].after == "merged"
    assert runs_store.get(str(d.id)) is not None  # record was created


def test_sync_pr_number_url_parser() -> None:
    """The URL → pr_number regex covers both http and trailing slashes."""
    from minions.sync import _pr_number_from_url

    assert _pr_number_from_url("https://github.com/owner/repo/pull/123") == 123
    assert _pr_number_from_url("http://x/owner/repo/pull/7/files") == 7
    assert _pr_number_from_url(None) is None
    assert _pr_number_from_url("https://github.com/owner/repo/issues/4") is None


def test_sync_pr_status_skips_records_without_pr_url(tmp_path: Path) -> None:
    store = EngineerRunStore(tmp_path / "runs.json")
    _saved_record(store, pr_url=None, decision_id="no-pr")
    report = sync_pr_status(
        store=store,
        open_github_client=lambda m: _client(lambda r: httpx.Response(500)),
        manifests={"p": _make_manifest()},
    )
    assert report.outcomes == []
