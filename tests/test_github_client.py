"""Tests for the scoped GitHub client. Uses httpx.MockTransport for the network."""

from __future__ import annotations

import json as json_lib
from collections.abc import Callable

import httpx
import pytest

from minions.github.client import GitHubClient, GitHubError, ProtectedBranchError


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> GitHubClient:
    return GitHubClient(token="ghp_test", repo="org/repo", transport=httpx.MockTransport(handler))


# ---------- Repo info ----------


def test_get_repo():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/repos/org/repo"
        return httpx.Response(
            200,
            json={
                "full_name": "org/repo",
                "default_branch": "main",
                "private": False,
                "html_url": "https://github.com/org/repo",
            },
        )

    repo = _client(handler).get_repo()
    assert repo.full_name == "org/repo"
    assert repo.default_branch == "main"
    assert repo.private is False


def test_authorization_and_accept_headers_set():
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update({k.lower(): v for k, v in request.headers.items()})
        return httpx.Response(
            200,
            json={
                "full_name": "x/y",
                "default_branch": "main",
                "private": False,
                "html_url": "",
            },
        )

    _client(handler).get_repo()
    assert seen.get("authorization") == "Bearer ghp_test"
    assert seen.get("accept") == "application/vnd.github+json"
    assert seen.get("x-github-api-version") == "2022-11-28"


def test_invalid_repo_format_raises():
    with pytest.raises(ValueError, match="owner/name"):
        GitHubClient(token="x", repo="just-a-name")
    with pytest.raises(ValueError, match="owner/name"):
        GitHubClient(token="x", repo="too/many/slashes")


# ---------- Issues ----------


def test_list_open_issues_filters_prs():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/org/repo/issues"
        return httpx.Response(
            200,
            json=[
                {
                    "number": 1,
                    "title": "Real issue",
                    "body": "...",
                    "state": "open",
                    "labels": [{"name": "bug"}],
                    "html_url": "u",
                    "user": {"login": "siddu"},
                },
                {
                    "number": 2,
                    "title": "PR pretending",
                    "pull_request": {},
                    "state": "open",
                    "labels": [],
                    "html_url": "u",
                    "user": {"login": "x"},
                    "body": "",
                },
            ],
        )

    issues = _client(handler).list_open_issues()
    assert len(issues) == 1
    assert issues[0].number == 1
    assert issues[0].labels == ["bug"]
    assert issues[0].user == "siddu"


def test_list_open_issues_passes_label_filter():
    seen_params: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_params.update(request.url.params)
        return httpx.Response(200, json=[])

    _client(handler).list_open_issues(label="mini:idea")
    assert seen_params.get("labels") == "mini:idea"
    assert seen_params.get("state") == "open"


def test_comment_on_issue():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json_lib.loads(request.content)
        assert body == {"body": "LGTM"}
        assert request.url.path == "/repos/org/repo/issues/7/comments"
        return httpx.Response(201, json={})

    _client(handler).comment_on_issue(number=7, body="LGTM")


def test_comment_on_pr_uses_issues_endpoint():
    def handler(request: httpx.Request) -> httpx.Response:
        # PR comments share the issues comments endpoint by GitHub design.
        assert request.url.path == "/repos/org/repo/issues/9/comments"
        return httpx.Response(201, json={})

    _client(handler).comment_on_pull_request(number=9, body="ok")


# ---------- Branching ----------


@pytest.mark.parametrize("protected", ["main", "master", "MAIN", "Master", "develop", "trunk"])
def test_create_branch_refuses_protected(protected: str):
    client = _client(lambda r: httpx.Response(200, json={}))
    with pytest.raises(ProtectedBranchError):
        client.create_branch(name=protected, base_sha="abc")


def test_create_branch_succeeds():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json_lib.loads(request.content)
        assert body == {"ref": "refs/heads/minions/eng/feat", "sha": "deadbeef"}
        return httpx.Response(201, json={"object": {"sha": "deadbeef"}})

    ref = _client(handler).create_branch(name="minions/eng/feat", base_sha="deadbeef")
    assert ref.name == "minions/eng/feat"
    assert ref.sha == "deadbeef"


# ---------- File commits ----------


def test_update_file_refuses_protected():
    client = _client(lambda r: httpx.Response(200, json={}))
    with pytest.raises(ProtectedBranchError):
        client.update_file(branch="main", path="README.md", content="hi", message="fix")


def test_update_file_creates_new_file():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json_lib.loads(request.content)
        # base64('hello') == aGVsbG8=
        assert body["content"] == "aGVsbG8="
        assert body["branch"] == "minions/eng/feat"
        assert body["message"] == "test"
        assert "sha" not in body  # creating, not updating
        return httpx.Response(201, json={"commit": {"sha": "newsha"}})

    sha = _client(handler).update_file(
        branch="minions/eng/feat", path="README.md", content="hello", message="test"
    )
    assert sha == "newsha"


def test_update_file_passes_existing_sha_when_updating():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json_lib.loads(request.content)
        assert body["sha"] == "blob-abc"
        return httpx.Response(200, json={"commit": {"sha": "newsha"}})

    sha = _client(handler).update_file(
        branch="minions/eng/feat",
        path="README.md",
        content=b"updated bytes",
        message="update",
        sha="blob-abc",
    )
    assert sha == "newsha"


def test_get_file_sha_returns_none_on_404():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    assert _client(handler).get_file_sha(path="missing.md", branch="x") is None


def test_get_file_sha_returns_blob_sha():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"sha": "blob-xyz", "type": "file"})

    assert _client(handler).get_file_sha(path="README.md", branch="main") == "blob-xyz"


# ---------- Pull requests ----------


def test_open_pr_refuses_protected_head():
    client = _client(lambda r: httpx.Response(200, json={}))
    with pytest.raises(ProtectedBranchError):
        client.open_pull_request(title="x", body="y", head="main")


def test_open_pr_defaults_to_draft_targeting_main():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json_lib.loads(request.content)
        assert body["draft"] is True
        assert body["base"] == "main"
        assert body["head"] == "minions/eng/feat"
        return httpx.Response(
            201,
            json={
                "number": 42,
                "title": "x",
                "body": "y",
                "state": "open",
                "head": {"ref": "minions/eng/feat"},
                "base": {"ref": "main"},
                "draft": True,
                "html_url": "https://github.com/org/repo/pull/42",
            },
        )

    pr = _client(handler).open_pull_request(title="x", body="y", head="minions/eng/feat")
    assert pr.number == 42
    assert pr.draft is True
    assert pr.head == "minions/eng/feat"


def test_no_merge_method_exists():
    """Hard guarantee — ProtectedBranch + branch protection are layered, but
    structurally the client cannot call a merge endpoint either."""
    client = _client(lambda r: httpx.Response(200, json={}))
    assert not hasattr(client, "merge_pull_request")
    assert not hasattr(client, "merge")
    assert not hasattr(client, "squash_merge")


# ---------- Errors ----------


def test_github_error_includes_status_and_message():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    client = _client(handler)
    with pytest.raises(GitHubError) as exc_info:
        client.get_repo()
    assert exc_info.value.status_code == 404
    assert "404" in str(exc_info.value)


def test_get_pr_check_status_swallows_stale_head_sha():
    """A vanished head SHA (force-push / rebase) must NOT raise; return (None, None)."""

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/pulls/7"):
            return httpx.Response(
                200,
                json={
                    "number": 7,
                    "title": "x",
                    "state": "open",
                    "draft": True,
                    "html_url": "u",
                    "head": {"ref": "feat", "sha": "deadbeef"},
                    "base": {"ref": "main"},
                },
            )
        if "/commits/deadbeef/check-runs" in req.url.path:
            return httpx.Response(404, json={"message": "Not Found"})
        return httpx.Response(500)

    client = _client(handler)
    conclusion, details = client.get_pr_check_status(7)
    assert conclusion is None
    assert details is None


def test_get_pr_check_status_propagates_unexpected_errors():
    """A 500 on check-runs is a real error and should still raise."""

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/pulls/7"):
            return httpx.Response(
                200,
                json={
                    "number": 7,
                    "title": "x",
                    "state": "open",
                    "draft": True,
                    "html_url": "u",
                    "head": {"ref": "feat", "sha": "deadbeef"},
                    "base": {"ref": "main"},
                },
            )
        return httpx.Response(500, json={"message": "boom"})

    client = _client(handler)
    with pytest.raises(GitHubError):
        client.get_pr_check_status(7)


def test_github_error_on_network_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated")

    client = _client(handler)
    with pytest.raises(GitHubError, match="network error"):
        client.get_repo()
