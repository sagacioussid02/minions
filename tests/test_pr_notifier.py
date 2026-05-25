"""Tests for §3.4 — operator review comment posted after PR open."""

from __future__ import annotations

import json as json_lib
from pathlib import Path
from typing import Callable

import httpx

from minions.crews.engineer import (
    EngineerOutput,
    FilePatch,
    _build_operator_review_comment,
    run_engineer_crew,
)
from minions.github.client import GitHubClient
from minions.models.decision import Decision, DecisionStatus, DecisionType
from minions.models.manifest import load_manifest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _decision() -> Decision:
    return Decision(
        project="Demo",
        type=DecisionType.FEATURE,
        summary="Add a CHANGELOG file",
        rationale="Track release notes",
        diff_or_plan="Create CHANGELOG.md.",
        risk="low",
        proposer_role="manager",
        proposer_agent_id="manager@Demo",
        proposer_display_name="Marcus",
        status=DecisionStatus.APPROVED,
    )


def _manifest():
    return load_manifest(REPO_ROOT / "projects" / "Demo.yaml")


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> GitHubClient:
    return GitHubClient(token="x", repo="org/repo", transport=httpx.MockTransport(handler))


def _override() -> EngineerOutput:
    return EngineerOutput(
        pr_title="docs: add CHANGELOG.md",
        pr_body="Initial changelog file.",
        files=[
            FilePatch(
                path="CHANGELOG.md",
                content="# Changelog\n",
                operation="create",
            )
        ],
    )


# ---- Pure builder ----------------------------------------------------------


def test_operator_comment_includes_decision_metadata() -> None:
    body = _build_operator_review_comment(
        _decision(), _manifest(), _override(), _override().files
    )
    assert "Operator review requested" in body
    assert "Demo" in body
    assert "Marcus" in body
    assert "low" in body  # risk
    assert "no merge capability" in body
    assert "minions decisions reject" in body
    assert "CHANGELOG.md" in body


# ---- End-to-end flow ------------------------------------------------------


def test_operator_comment_is_posted_after_pr_open() -> None:
    posted_comments: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if req.method == "GET" and path == "/repos/org/repo":
            return httpx.Response(200, json={"full_name": "org/repo", "default_branch": "main", "private": False, "html_url": "u"})
        if req.method == "GET" and path == "/repos/org/repo/git/ref/heads/main":
            return httpx.Response(200, json={"object": {"sha": "basesha"}})
        if req.method == "GET" and path.startswith("/repos/org/repo/git/ref/heads/minions/"):
            return httpx.Response(404, json={"message": "Not Found"})
        if req.method == "POST" and path == "/repos/org/repo/git/refs":
            return httpx.Response(201, json={"object": {"sha": "basesha"}})
        if req.method == "GET" and path.startswith("/repos/org/repo/contents/"):
            return httpx.Response(404, json={"message": "Not Found"})
        if req.method == "PUT" and path == "/repos/org/repo/contents/CHANGELOG.md":
            return httpx.Response(201, json={"commit": {"sha": "newcommit"}})
        if req.method == "POST" and path == "/repos/org/repo/pulls":
            body = json_lib.loads(req.content)
            return httpx.Response(
                201,
                json={
                    "number": 42,
                    "title": body["title"],
                    "body": body["body"],
                    "state": "open",
                    "head": {"ref": body["head"]},
                    "base": {"ref": "main"},
                    "draft": True,
                    "html_url": "https://github.com/org/repo/pull/42",
                },
            )
        if req.method == "POST" and path == "/repos/org/repo/issues/42/comments":
            posted_comments.append(json_lib.loads(req.content)["body"])
            return httpx.Response(201, json={})
        return httpx.Response(500, json={"message": f"unexpected: {req.method} {path}"})

    result = run_engineer_crew(
        _decision(),
        _manifest(),
        github=_client(handler),
        dry_run=False,
        api_key=None,  # no TTL review — only the operator comment should fire
        output_override=_override(),
    )

    assert result.operator_comment_posted is True
    assert len(posted_comments) == 1
    assert "Operator review requested" in posted_comments[0]
    assert str(result.decision_id) in posted_comments[0]


def test_operator_comment_failure_does_not_break_pr() -> None:
    """If the comment endpoint 500s, the PR result should still report success."""

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if req.method == "GET" and path == "/repos/org/repo":
            return httpx.Response(200, json={"full_name": "org/repo", "default_branch": "main", "private": False, "html_url": "u"})
        if req.method == "GET" and path == "/repos/org/repo/git/ref/heads/main":
            return httpx.Response(200, json={"object": {"sha": "basesha"}})
        if req.method == "GET" and path.startswith("/repos/org/repo/git/ref/heads/minions/"):
            return httpx.Response(404, json={"message": "Not Found"})
        if req.method == "POST" and path == "/repos/org/repo/git/refs":
            return httpx.Response(201, json={"object": {"sha": "basesha"}})
        if req.method == "GET" and path.startswith("/repos/org/repo/contents/"):
            return httpx.Response(404, json={"message": "Not Found"})
        if req.method == "PUT" and path == "/repos/org/repo/contents/CHANGELOG.md":
            return httpx.Response(201, json={"commit": {"sha": "newcommit"}})
        if req.method == "POST" and path == "/repos/org/repo/pulls":
            return httpx.Response(
                201,
                json={
                    "number": 7,
                    "title": "t",
                    "body": "b",
                    "state": "open",
                    "head": {"ref": "minions/eng/x"},
                    "base": {"ref": "main"},
                    "draft": True,
                    "html_url": "https://github.com/org/repo/pull/7",
                },
            )
        if req.method == "POST" and path == "/repos/org/repo/issues/7/comments":
            return httpx.Response(503, json={"message": "GitHub down"})
        return httpx.Response(500, json={"message": "unexpected"})

    result = run_engineer_crew(
        _decision(),
        _manifest(),
        github=_client(handler),
        dry_run=False,
        api_key=None,
        output_override=_override(),
    )

    assert result.pr_url == "https://github.com/org/repo/pull/7"
    assert result.operator_comment_posted is False
