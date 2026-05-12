"""Tests for guardrail_blocked instrumentation.

Covers:
  * activity.record_guardrail_block writes a well-formed entry.
  * activity.guardrail_blocks filters by ``since`` and orders newest first.
  * The GitHub client emits a guardrail_blocked event when it refuses a
    protected-branch operation (real Layer-2 evidence, not a mock).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from minions import activity
from minions.activity import (
    guardrail_blocks,
    read_log,
    record_guardrail_block,
    set_log_path,
)
from minions.github.client import GitHubClient, ProtectedBranchError


@pytest.fixture(autouse=True)
def _isolated_log(tmp_path: Path) -> Path:
    p = tmp_path / "activity.jsonl"
    set_log_path(p)
    yield p
    activity._log_path_override = None


def _client() -> GitHubClient:
    return GitHubClient(
        token="ghp_test",
        repo="org/repo",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})),
    )


def test_record_guardrail_block_writes_expected_entry() -> None:
    record_guardrail_block(
        layer="layer2_tooling",
        kind="protected_branch",
        details="push refused on 'main'",
        project="demo",
    )
    entries = read_log()
    assert len(entries) == 1
    e = entries[0]
    assert e.event == "guardrail_blocked"
    assert e.crew == "guardrail:layer2_tooling"
    assert e.agents == ("protected_branch",)
    assert e.error == "push refused on 'main'"
    assert e.project == "demo"


def test_record_guardrail_block_truncates_long_details() -> None:
    record_guardrail_block(
        layer="layer1_prompt",
        kind="env_read",
        details="x" * 500,
    )
    e = read_log()[0]
    assert e.error is not None
    assert len(e.error) == 200


def test_guardrail_blocks_filters_by_since_and_orders_newest_first() -> None:
    # Synthesize three entries spanning 3 days.
    now = datetime.now(tz=UTC)
    for i, age_days in enumerate([3, 1, 0]):
        activity.append(
            activity.ActivityEntry(
                timestamp=now - timedelta(days=age_days),
                event="guardrail_blocked",
                run_id=f"r{i}",
                crew="guardrail:layer2_tooling",
                project="p",
                decision_id="",
                agents=("protected_branch",),
                error=f"detail {i}",
            )
        )
    # Also add a non-guardrail event that must be ignored.
    activity.append(
        activity.ActivityEntry(
            timestamp=now,
            event="crew_started",
            run_id="rx",
            crew="planning",
            project="p",
            decision_id="",
            agents=("manager",),
        )
    )

    recent = guardrail_blocks(since=now - timedelta(days=2))
    # Drops the 3-day-old one.
    assert len(recent) == 2
    # Newest first.
    assert recent[0].timestamp > recent[1].timestamp


def test_create_branch_protected_emits_guardrail_event() -> None:
    with pytest.raises(ProtectedBranchError):
        _client().create_branch(name="main", base_sha="abc")
    blocks = guardrail_blocks()
    assert len(blocks) == 1
    e = blocks[0]
    assert e.crew == "guardrail:layer2_tooling"
    assert e.agents == ("protected_branch",)
    assert e.error is not None
    assert "create_branch" in e.error
    assert "'main'" in e.error
    assert "org/repo" in e.error


def test_update_file_protected_emits_guardrail_event() -> None:
    with pytest.raises(ProtectedBranchError):
        _client().update_file(
            branch="develop",
            path="README.md",
            content="hi",
            message="x",
        )
    blocks = guardrail_blocks()
    assert len(blocks) == 1
    assert blocks[0].error is not None
    assert "update_file" in blocks[0].error
    assert "'develop'" in blocks[0].error


def test_open_pr_protected_head_emits_guardrail_event() -> None:
    with pytest.raises(ProtectedBranchError):
        _client().open_pull_request(title="x", body="y", head="master")
    blocks = guardrail_blocks()
    assert len(blocks) == 1
    assert blocks[0].error is not None
    assert "open_pull_request" in blocks[0].error
    assert "'master'" in blocks[0].error


def test_safe_branch_does_not_emit() -> None:
    # Real successful create_branch — no guardrail event should be recorded.
    transport = httpx.MockTransport(
        lambda r: httpx.Response(201, json={"object": {"sha": "deadbeef"}})
    )
    client = GitHubClient(token="t", repo="org/repo", transport=transport)
    client.create_branch(name="minions/eng/feat", base_sha="x")
    assert guardrail_blocks() == []
