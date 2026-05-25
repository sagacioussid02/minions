"""Scoped GitHub REST client.

By design this client cannot:
  - push commits to ``main``/``master``/``trunk``/``develop`` (refused at
    the client level AND by server-side branch protection on the repo)

These are belt-and-suspenders against any prompt-injection or mistake that
gets past the agent's safety preamble. PR merge is allowed only through the
explicit ``merge_pull_request`` method, which lets GitHub branch protection make
the final decision.
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

    def list_issue_comments(self, *, number: int, per_page: int = 30) -> list[dict[str, Any]]:
        """List conversation comments on an issue / PR (most-recent last).

        PR comments live on the issue-comments endpoint; PR reviews
        (file-level inline) live on a separate one we don't need here.
        Returns flattened ``{user, created_at, body}`` dicts.
        """
        r = self._request(
            "GET",
            f"/repos/{self.repo}/issues/{number}/comments",
            params={"per_page": per_page},
        )
        out: list[dict[str, Any]] = []
        for c in r.json():
            user_obj = c.get("user") or {}
            out.append(
                {
                    "user": user_obj.get("login", ""),
                    "created_at": c.get("created_at", ""),
                    "body": c.get("body", "") or "",
                }
            )
        return out

    def comment_on_issue(self, *, number: int, body: str) -> None:
        self._request(
            "POST",
            f"/repos/{self.repo}/issues/{number}/comments",
            json={"body": body},
        )

    def create_issue(
        self,
        *,
        title: str,
        body: str,
        labels: list[str] | None = None,
    ) -> Issue:
        """Create a new GitHub issue. No assignees, no milestones — keep it minimal."""
        payload: dict[str, Any] = {"title": title, "body": body}
        if labels:
            payload["labels"] = labels
        r = self._request("POST", f"/repos/{self.repo}/issues", json=payload)
        return _normalize_issue(r.json())

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

    def delete_branch(self, *, name: str) -> None:
        """Delete a branch ref. Refuses protected branches.

        Used by the engineer crew to roll back a stranded branch when PR
        creation fails, and by the branch sweeper to garbage-collect old
        engineer-created branches that never got a PR opened.
        """
        self._check_not_protected(name)
        # GitHub returns 204 on success, 422 if the ref does not exist. Treat
        # 422/404 as a no-op so callers can be idempotent.
        try:
            self._request("DELETE", f"/repos/{self.repo}/git/refs/heads/{name}")
        except GitHubError as e:
            if e.status_code in (404, 422):
                return
            raise

    def list_branches(self, *, prefix: str | None = None) -> list[BranchRef]:
        """List branches in the repo, optionally filtered by name prefix.

        Paginates through all branches; intended for low-traffic admin paths
        (the branch sweeper) — not the engineer hot path.
        """
        out: list[BranchRef] = []
        page = 1
        while True:
            r = self._request(
                "GET",
                f"/repos/{self.repo}/branches",
                params={"per_page": "100", "page": str(page)},
            )
            items = r.json()
            if not items:
                break
            for item in items:
                name = item.get("name")
                sha = (item.get("commit") or {}).get("sha")
                if not isinstance(name, str) or not isinstance(sha, str):
                    continue
                if prefix is not None and not name.startswith(prefix):
                    continue
                out.append(BranchRef(name=name, sha=sha))
            if len(items) < 100:
                break
            page += 1
        return out

    def list_branch_commits(self, *, branch: str, limit: int = 100) -> list[dict[str, Any]]:
        """Return raw commit objects for a branch (newest first).

        Each item has at least: ``sha``, ``commit.message``, ``commit.author``
        (with ``name``, ``email``, ``date``). Used by the branch sweeper to
        verify ownership trailers and age before deleting.
        """
        r = self._request(
            "GET",
            f"/repos/{self.repo}/commits",
            params={"sha": branch, "per_page": str(min(limit, 100))},
        )
        items = r.json()
        return items if isinstance(items, list) else []

    def find_pull_request_for_branch(self, *, branch: str) -> PullRequest | None:
        """Return the most recent PR whose head ref is ``branch``, any state."""
        r = self._request(
            "GET",
            f"/repos/{self.repo}/pulls",
            params={"head": f"{self.repo.split('/')[0]}:{branch}", "state": "all", "per_page": "1"},
        )
        items = r.json()
        if not isinstance(items, list) or not items:
            return None
        return _normalize_pull_request(items[0])

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

    def get_text_file(self, *, path: str, branch: str) -> str | None:
        """Read a UTF-8 text file through the GitHub Contents API.

        This is intentionally read-only and returns None for missing files,
        directories, non-base64 payloads, or files GitHub does not inline.
        """
        try:
            r = self._request("GET", f"/repos/{self.repo}/contents/{path}", params={"ref": branch})
        except GitHubError as e:
            if e.status_code == 404:
                return None
            raise
        body = r.json()
        if isinstance(body, list) or body.get("encoding") != "base64":
            return None
        raw = body.get("content")
        if not isinstance(raw, str):
            return None
        try:
            return base64.b64decode(raw).decode("utf-8", errors="ignore")
        except (ValueError, TypeError):
            return None

    def list_files(self, *, branch: str) -> list[str]:
        """List repository file paths using the read-only git tree endpoint."""
        r = self._request(
            "GET",
            f"/repos/{self.repo}/git/trees/{branch}",
            params={"recursive": "1"},
        )
        return [
            item["path"]
            for item in r.json().get("tree", [])
            if item.get("type") == "blob" and isinstance(item.get("path"), str)
        ]

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

    def close_pull_request(self, *, number: int) -> PullRequest:
        """Close an open PR without merging it."""
        r = self._request(
            "PATCH",
            f"/repos/{self.repo}/pulls/{number}",
            json={"state": "closed"},
        )
        return _normalize_pull_request(r.json())

    def get_pr_merge_state(self, number: int) -> str | None:
        """Return GitHub's mergeability state for a PR when available.

        GitHub computes this field asynchronously, so callers should treat
        ``None`` or ``"unknown"`` as inconclusive and retry on a later sweep.
        """
        return self.get_pull_request(number).mergeable_state

    def merge_pull_request(
        self,
        *,
        number: int,
        commit_title: str | None = None,
        commit_message: str | None = None,
        method: str = "squash",
    ) -> bool:
        """Ask GitHub to merge a PR.

        This does not bypass branch protection. GitHub returns 405/409/422 when
        required reviews, checks, or repo rules block the merge; callers should
        treat those as a human/operator handoff.
        """
        body: dict[str, Any] = {"merge_method": method}
        if commit_title:
            body["commit_title"] = commit_title
        if commit_message:
            body["commit_message"] = commit_message
        r = self._request("PUT", f"/repos/{self.repo}/pulls/{number}/merge", json=body)
        return bool(r.json().get("merged", False))

    def get_pr_check_status(self, number: int) -> tuple[str | None, str | None]:
        """Return ``(conclusion, details_url)`` for the latest CI on a PR.

        ``conclusion`` is one of ``"success"`` / ``"failure"`` / ``"pending"`` /
        ``None`` (no checks configured yet). Aggregates across all check-runs on
        the PR's head SHA: any failure wins; otherwise any in-flight check makes
        it ``"pending"``; otherwise ``"success"`` when ≥1 check completed.
        """
        pr = self.get_pull_request(number)
        if not pr.head_sha:
            return None, None
        # Best-effort read. The head SHA can disappear from the repo (force-push,
        # rebase, branch deleted before sweep) and the check-runs endpoint also
        # 403s on some private-repo + token combinations. Either way, "unknown"
        # is the right answer — the sweep should not error on a stale PR.
        try:
            r = self._request("GET", f"/repos/{self.repo}/commits/{pr.head_sha}/check-runs")
        except GitHubError as e:
            if e.status_code in {403, 404, 422}:
                return None, None
            raise
        runs = r.json().get("check_runs", []) or []
        if not runs:
            return None, None

        details_url: str | None = None
        had_failure = False
        had_pending = False
        for run in runs:
            status = (run.get("status") or "").lower()
            concl = (run.get("conclusion") or "").lower() or None
            if status != "completed":
                had_pending = True
                continue
            if concl in {"failure", "timed_out", "cancelled", "action_required"}:
                had_failure = True
                if not details_url:
                    details_url = run.get("html_url") or run.get("details_url")

        if had_failure:
            return "failure", details_url
        if had_pending:
            return "pending", None
        return "success", None

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
        head_sha=head_obj.get("sha") if isinstance(head_obj, dict) else None,
        base=base_obj.get("ref", "") if isinstance(base_obj, dict) else "",
        draft=bool(item.get("draft", False)),
        html_url=item.get("html_url") or "",
        merged=bool(item.get("merged", False)),
        merged_at=item.get("merged_at"),
        closed_at=item.get("closed_at"),
        mergeable_state=item.get("mergeable_state"),
    )
