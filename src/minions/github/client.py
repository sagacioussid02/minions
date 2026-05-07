"""Scoped GitHub REST client.

By design this client cannot:
  - merge a pull request (no method exists — see test_no_merge_method_exists)
  - push commits to ``main``/``master``/``trunk``/``develop`` (refused at
    the client level AND by server-side branch protection on the repo)

These are belt-and-suspenders against any prompt-injection or mistake that
gets past the agent's safety preamble.
"""

from __future__ import annotations

import base64
from typing import Any

import httpx

from minions.github.models import BranchRef, Issue, PullRequest, Repo


class GitHubError(RuntimeError):
    """A GitHub API call failed."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ProtectedBranchError(RuntimeError):
    """The caller attempted to write to a protected branch name."""


_PROTECTED_BRANCHES: frozenset[str] = frozenset({"main", "master", "trunk", "develop"})


class GitHubClient:
    """Thin REST client scoped to a single repo."""

    def __init__(
        self,
        *,
        token: str,
        repo: str,
        api_base: str = "https://api.github.com",
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        if "/" not in repo or repo.count("/") != 1:
            raise ValueError(f"repo must be 'owner/name', got {repo!r}")
        self.repo = repo
        self.api_base = api_base.rstrip("/")
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "minions-org/0.0.1",
        }
        self._client = httpx.Client(
            base_url=self.api_base,
            headers=headers,
            timeout=timeout,
            transport=transport,
        )

    # ---- Lifecycle ----

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> GitHubClient:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ---- Internals ----

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any | None = None,
        params: Any | None = None,
    ) -> httpx.Response:
        try:
            response = self._client.request(method, path, json=json, params=params)
        except httpx.RequestError as e:
            raise GitHubError(f"network error on {method} {path}: {e}") from e
        if response.status_code >= 400:
            try:
                detail = response.json().get("message", response.text)
            except Exception:
                detail = response.text
            raise GitHubError(
                f"GitHub {method} {path} returned {response.status_code}: {detail}",
                status_code=response.status_code,
            )
        return response

    @staticmethod
    def _check_not_protected(branch: str) -> None:
        if branch.lower() in _PROTECTED_BRANCHES:
            raise ProtectedBranchError(
                f"refusing to operate on protected branch {branch!r}. "
                f"Create a feature branch (e.g., minions/<role>/<summary>) and open a PR."
            )

    # ---- Repo info ----

    def get_repo(self) -> Repo:
        r = self._request("GET", f"/repos/{self.repo}")
        return Repo.model_validate(r.json())

    def get_default_branch(self) -> str:
        return self.get_repo().default_branch

    def get_branch_ref(self, branch: str) -> BranchRef:
        r = self._request("GET", f"/repos/{self.repo}/git/ref/heads/{branch}")
        body = r.json()
        return BranchRef(name=branch, sha=body["object"]["sha"])

    # ---- Issues ----

    def list_open_issues(
        self,
        *,
        label: str | None = None,
        per_page: int = 50,
    ) -> list[Issue]:
        """List open issues. PRs (which appear in the issues feed) are filtered out."""
        params: dict[str, Any] = {"state": "open", "per_page": per_page}
        if label:
            params["labels"] = label
        r = self._request("GET", f"/repos/{self.repo}/issues", params=params)
        out: list[Issue] = []
        for item in r.json():
            if "pull_request" in item:
                continue
            out.append(_normalize_issue(item))
        return out

    def get_issue(self, number: int) -> Issue:
        r = self._request("GET", f"/repos/{self.repo}/issues/{number}")
        return _normalize_issue(r.json())

    def comment_on_issue(self, *, number: int, body: str) -> None:
        self._request(
            "POST",
            f"/repos/{self.repo}/issues/{number}/comments",
            json={"body": body},
        )

    # ---- Branching ----

    def create_branch(self, *, name: str, base_sha: str) -> BranchRef:
        """Create a new ref. Refuses protected branch names."""
        self._check_not_protected(name)
        r = self._request(
            "POST",
            f"/repos/{self.repo}/git/refs",
            json={"ref": f"refs/heads/{name}", "sha": base_sha},
        )
        return BranchRef(name=name, sha=r.json()["object"]["sha"])

    # ---- File commits (single file via Contents API) ----

    def get_file_sha(self, *, path: str, branch: str) -> str | None:
        """Return the blob SHA for an existing file, or None if it doesn't exist."""
        try:
            r = self._request("GET", f"/repos/{self.repo}/contents/{path}", params={"ref": branch})
        except GitHubError as e:
            if e.status_code == 404:
                return None
            raise
        body = r.json()
        if isinstance(body, list):
            # Path is a directory; not what the caller asked for.
            return None
        return body.get("sha")

    def update_file(
        self,
        *,
        branch: str,
        path: str,
        content: bytes | str,
        message: str,
        sha: str | None = None,
    ) -> str:
        """Create or update a single file on a branch via the Contents API.

        For an existing file, ``sha`` MUST be the file's current blob SHA
        (use :meth:`get_file_sha`). Returns the resulting commit SHA.
        Refuses protected branches.
        """
        self._check_not_protected(branch)
        content_bytes = content.encode("utf-8") if isinstance(content, str) else content
        body: dict[str, Any] = {
            "message": message,
            "branch": branch,
            "content": base64.b64encode(content_bytes).decode("ascii"),
        }
        if sha is not None:
            body["sha"] = sha
        r = self._request("PUT", f"/repos/{self.repo}/contents/{path}", json=body)
        return r.json()["commit"]["sha"]

    # ---- Pull requests ----

    def open_pull_request(
        self,
        *,
        title: str,
        body: str,
        head: str,
        base: str = "main",
        draft: bool = True,
    ) -> PullRequest:
        """Open a PR. Default is draft. Refuses head=protected branch."""
        if head.lower() in _PROTECTED_BRANCHES:
            raise ProtectedBranchError(
                f"refusing to open a PR with head={head!r}; "
                f"feature branches must be of the form minions/<role>/<summary>."
            )
        r = self._request(
            "POST",
            f"/repos/{self.repo}/pulls",
            json={
                "title": title,
                "body": body,
                "head": head,
                "base": base,
                "draft": draft,
            },
        )
        return _normalize_pull_request(r.json())

    def comment_on_pull_request(self, *, number: int, body: str) -> None:
        # PR comments use the issues comments endpoint.
        self.comment_on_issue(number=number, body=body)

    def get_pull_request(self, number: int) -> PullRequest:
        r = self._request("GET", f"/repos/{self.repo}/pulls/{number}")
        return _normalize_pull_request(r.json())

    def list_pull_request_files(self, number: int, *, per_page: int = 30) -> list[dict[str, Any]]:
        """Return PR files: filename, status, additions, deletions, patch (when present).

        GitHub caps per_page at 100 and excludes patches over 5MB. This is good
        enough for an audit pass — large PRs get summarized, not whole-diffed.
        """
        r = self._request(
            "GET",
            f"/repos/{self.repo}/pulls/{number}/files",
            params={"per_page": per_page},
        )
        out: list[dict[str, Any]] = []
        for f in r.json():
            out.append(
                {
                    "filename": f.get("filename", ""),
                    "status": f.get("status", ""),
                    "additions": int(f.get("additions", 0) or 0),
                    "deletions": int(f.get("deletions", 0) or 0),
                    "patch": f.get("patch"),  # may be None for binary / huge files
                }
            )
        return out

    # No merge_pull_request method exists by design.


def _normalize_issue(item: dict[str, Any]) -> Issue:
    labels: list[str] = []
    for raw_label in item.get("labels") or []:
        if isinstance(raw_label, dict) and isinstance(raw_label.get("name"), str):
            labels.append(raw_label["name"])
        elif isinstance(raw_label, str):
            labels.append(raw_label)
    user_obj = item.get("user") or {}
    return Issue(
        number=item["number"],
        title=item.get("title") or "",
        body=item.get("body"),
        state=item.get("state") or "open",
        labels=labels,
        html_url=item.get("html_url") or "",
        user=user_obj.get("login") if isinstance(user_obj, dict) else None,
    )


def _normalize_pull_request(item: dict[str, Any]) -> PullRequest:
    head_obj = item.get("head") or {}
    base_obj = item.get("base") or {}
    return PullRequest(
        number=item["number"],
        title=item.get("title") or "",
        body=item.get("body"),
        state=item.get("state") or "open",
        head=head_obj.get("ref", "") if isinstance(head_obj, dict) else "",
        base=base_obj.get("ref", "") if isinstance(base_obj, dict) else "",
        draft=bool(item.get("draft", False)),
        html_url=item.get("html_url") or "",
        merged=bool(item.get("merged", False)),
        merged_at=item.get("merged_at"),
        closed_at=item.get("closed_at"),
    )
