"""Tests for the engineer crew — mocks the LLM via output_override and the
GitHub client via httpx.MockTransport."""

from __future__ import annotations

import json as json_lib
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from minions.crews.engineer import (
    EngineerOutput,
    FilePatch,
    branch_name_for_decision,
    filter_files,
    is_forbidden_path,
    run_engineer_crew,
    slugify,
)
from minions.github.client import GitHubClient
from minions.models.decision import Decision, DecisionStatus, DecisionType
from minions.models.manifest import load_manifest

REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------- Pure helpers ----------


@pytest.mark.parametrize(
    "given,expected_prefix",
    [
        ("Add docstrings to README", "add-docstrings-to-readme"),
        ("[DRY RUN] Sprint proposal for Demo", "dry-run-sprint-proposal-for-Demo"),
        ("!!!", "change"),  # falls back when nothing alphanumeric
    ],
)
def test_slugify(given, expected_prefix):
    out = slugify(given)
    assert out == expected_prefix or out.startswith(expected_prefix)


def test_branch_name_includes_decision_id_suffix():
    first = _decision()
    second = _decision()
    second.summary = first.summary

    assert branch_name_for_decision(first).startswith("minions/eng/add-a-changelog-file-")
    assert branch_name_for_decision(second).startswith("minions/eng/add-a-changelog-file-")
    assert branch_name_for_decision(first) != branch_name_for_decision(second)


@pytest.mark.parametrize(
    "path,forbidden",
    [
        (".github/workflows/ci.yml", True),
        (".env", True),
        (".env.local", True),
        ("secrets/token.json", True),
        ("path/to/credentials.yaml", True),
        ("certs/server.pem", True),
        ("certs/server.key", True),
        (".aws/credentials", True),
        ("README.md", False),
        ("src/main.py", False),
        ("docs/setup.md", False),
        ("package.json", False),
        # Allowlist: env templates are not secrets — engineer crew may write them.
        (".env.example", False),
        (".env.sample", False),
        (".env.template", False),
        (".env.dist", False),
    ],
)
def test_is_forbidden_path(path, forbidden):
    assert is_forbidden_path(path) is forbidden


def test_filter_files_drops_forbidden():
    files = [
        FilePatch(path="README.md", content="x"),
        FilePatch(path=".github/workflows/deploy.yml", content="y"),
        FilePatch(path="src/foo.py", content="z"),
    ]
    allowed, rejected = filter_files(files)
    assert [f.path for f in allowed] == ["README.md", "src/foo.py"]
    assert rejected == [".github/workflows/deploy.yml"]


def test_filter_files_caps_at_max():
    files = [FilePatch(path=f"f{i}.md", content="x") for i in range(10)]
    allowed, rejected = filter_files(files)
    assert len(allowed) == 5  # MAX_FILES_PER_PR
    assert rejected == []  # excess is dropped silently (cap, not "rejected")


# ---------- Engineer crew flow ----------


def _decision(status: DecisionStatus = DecisionStatus.APPROVED) -> Decision:
    return Decision(
        project="Demo",
        type=DecisionType.FEATURE,
        summary="Add a CHANGELOG file",
        rationale="Track release notes",
        diff_or_plan="Create CHANGELOG.md with an initial entry.",
        proposer_role="manager",
        proposer_agent_id="manager@Demo",
        proposer_display_name="Marcus",
        status=status,
    )


def _manifest():
    return load_manifest(REPO_ROOT / "projects" / "Demo.yaml")


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> GitHubClient:
    return GitHubClient(token="x", repo="org/repo", transport=httpx.MockTransport(handler))


def test_skips_unapproved_decision():
    handler_called = {"n": 0}

    def handler(_req: httpx.Request) -> httpx.Response:
        handler_called["n"] += 1
        return httpx.Response(200, json={})

    client = _client(handler)
    result = run_engineer_crew(
        _decision(DecisionStatus.PENDING),
        _manifest(),
        github=client,
        dry_run=True,
    )
    assert result.skipped is True
    assert (
        "PENDING" in (result.skip_reason or "") or "pending" in (result.skip_reason or "").lower()
    )
    assert handler_called["n"] == 0  # never touched the API


def test_dry_run_returns_synthetic_pr_without_mutation():
    routes_seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        routes_seen.append(f"{req.method} {req.url.path}")
        if req.method == "GET" and req.url.path == "/repos/org/repo":
            return httpx.Response(
                200,
                json={
                    "full_name": "org/repo",
                    "default_branch": "main",
                    "private": False,
                    "html_url": "u",
                },
            )
        if req.method == "GET" and req.url.path == "/repos/org/repo/git/ref/heads/main":
            return httpx.Response(200, json={"object": {"sha": "deadbeef"}})
        if req.method == "GET" and req.url.path.startswith(
            "/repos/org/repo/git/ref/heads/minions/"
        ):
            # Branch-exists check: our branch shouldn't exist yet.
            return httpx.Response(404, json={"message": "Not Found"})
        return httpx.Response(500, json={"message": "unexpected route in test"})

    client = _client(handler)
    result = run_engineer_crew(
        _decision(),
        _manifest(),
        github=client,
        dry_run=True,
    )
    # Dry-run: no branch creation, no commits, no PR open should appear in routes.
    write_routes = [r for r in routes_seen if r.startswith(("POST", "PUT"))]
    assert write_routes == []
    assert result.dry_run is True
    assert result.skipped is False
    assert result.pr_url is not None and "DRY RUN" in result.pr_url
    assert result.branch_name and result.branch_name.startswith("minions/eng/")


def test_live_path_creates_branch_commits_files_opens_pr():
    """End-to-end live flow with a mocked GitHub backend.

    LLM is bypassed via output_override so no real Claude calls happen.
    """
    routes: list[tuple[str, str]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        routes.append((req.method, req.url.path))
        path = req.url.path
        if req.method == "GET" and path == "/repos/org/repo":
            return httpx.Response(
                200,
                json={
                    "full_name": "org/repo",
                    "default_branch": "main",
                    "private": False,
                    "html_url": "u",
                },
            )
        if req.method == "GET" and path == "/repos/org/repo/git/ref/heads/main":
            return httpx.Response(200, json={"object": {"sha": "basesha"}})
        if req.method == "GET" and path.startswith("/repos/org/repo/git/ref/heads/minions/"):
            return httpx.Response(404, json={"message": "Not Found"})
        if req.method == "POST" and path == "/repos/org/repo/git/refs":
            body = json_lib.loads(req.content)
            assert body["ref"].startswith("refs/heads/minions/eng/")
            assert body["sha"] == "basesha"
            return httpx.Response(201, json={"object": {"sha": "basesha"}})
        if req.method == "GET" and path.startswith("/repos/org/repo/contents/"):
            # File doesn't exist → engineer is creating it
            return httpx.Response(404, json={"message": "Not Found"})
        if req.method == "PUT" and path == "/repos/org/repo/contents/CHANGELOG.md":
            body = json_lib.loads(req.content)
            assert body["branch"].startswith("minions/eng/")
            assert "content" in body  # base64
            return httpx.Response(201, json={"commit": {"sha": "newcommit"}})
        if req.method == "POST" and path == "/repos/org/repo/pulls":
            body = json_lib.loads(req.content)
            assert body["draft"] is True
            assert body["base"] == "main"
            assert body["head"].startswith("minions/eng/")
            return httpx.Response(
                201,
                json={
                    "number": 1,
                    "title": body["title"],
                    "body": body["body"],
                    "state": "open",
                    "head": {"ref": body["head"]},
                    "base": {"ref": "main"},
                    "draft": True,
                    "html_url": "https://github.com/org/repo/pull/1",
                },
            )
        return httpx.Response(
            500, json={"message": f"unexpected route in test: {req.method} {path}"}
        )

    client = _client(handler)
    override = EngineerOutput(
        pr_title="docs: add CHANGELOG.md",
        pr_body="Initial changelog file with the v0.1 entry.",
        files=[
            FilePatch(
                path="CHANGELOG.md",
                content="# Changelog\n\n## [0.1.0]\n- initial release\n",
                operation="create",
            )
        ],
    )
    # api_key=None, output_override bypasses LLM AND skips the TTL review (which needs an API key).
    result = run_engineer_crew(
        _decision(),
        _manifest(),
        github=client,
        dry_run=False,
        api_key=None,
        output_override=override,
    )
    assert result.skipped is False
    assert result.dry_run is False
    assert result.pr_url == "https://github.com/org/repo/pull/1"
    assert result.pr_number == 1
    assert result.branch_name and result.branch_name.startswith("minions/eng/")
    assert result.files_changed == ["CHANGELOG.md"]
    # No TTL review without an api_key
    assert result.review_comment is None

    # Verify the right routes were hit.
    methods_paths = [(m, p) for m, p in routes if m in {"POST", "PUT"}]
    assert any(m == "POST" and p == "/repos/org/repo/git/refs" for m, p in methods_paths)
    assert any(
        m == "PUT" and p == "/repos/org/repo/contents/CHANGELOG.md" for m, p in methods_paths
    )
    assert any(m == "POST" and p == "/repos/org/repo/pulls" for m, p in methods_paths)


def test_refuses_when_branch_already_exists():
    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if req.method == "GET" and path == "/repos/org/repo":
            return httpx.Response(
                200,
                json={
                    "full_name": "org/repo",
                    "default_branch": "main",
                    "private": False,
                    "html_url": "u",
                },
            )
        if req.method == "GET" and path == "/repos/org/repo/git/ref/heads/main":
            return httpx.Response(200, json={"object": {"sha": "basesha"}})
        if req.method == "GET" and path.startswith("/repos/org/repo/git/ref/heads/minions/"):
            return httpx.Response(200, json={"object": {"sha": "existing"}})  # branch EXISTS
        return httpx.Response(500, json={"message": f"unexpected: {path}"})

    client = _client(handler)
    override = EngineerOutput(
        pr_title="x",
        pr_body="x",
        files=[FilePatch(path="X.md", content="x")],
    )
    result = run_engineer_crew(
        _decision(),
        _manifest(),
        github=client,
        dry_run=False,
        api_key=None,
        output_override=override,
    )
    assert result.skipped is True
    assert "already exists" in (result.skip_reason or "")


def test_skips_when_only_forbidden_files():
    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if req.method == "GET" and path == "/repos/org/repo":
            return httpx.Response(
                200,
                json={
                    "full_name": "org/repo",
                    "default_branch": "main",
                    "private": False,
                    "html_url": "u",
                },
            )
        if req.method == "GET" and path == "/repos/org/repo/git/ref/heads/main":
            return httpx.Response(200, json={"object": {"sha": "basesha"}})
        return httpx.Response(500, json={"message": f"unexpected: {path}"})

    client = _client(handler)
    override = EngineerOutput(
        pr_title="x",
        pr_body="x",
        files=[
            FilePatch(path=".github/workflows/ci.yml", content="x"),
            FilePatch(path=".env", content="y"),
        ],
    )
    result = run_engineer_crew(
        _decision(),
        _manifest(),
        github=client,
        dry_run=False,
        api_key=None,
        output_override=override,
    )
    assert result.skipped is True
    assert ".github/workflows/ci.yml" in result.files_rejected
    assert ".env" in result.files_rejected
