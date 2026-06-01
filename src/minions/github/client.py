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
import re
from typing import Any

import httpx

from minions.github.models import BranchRef, FailingCheckLog, Issue, PullRequest, Repo


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

    def _check_not_protected(self, branch: str, *, operation: str = "push") -> None:
        if branch.lower() in _PROTECTED_BRANCHES:
            from minions.activity import record_guardrail_block

            details = f"{operation} refused on {branch!r} in {self.repo}"
            record_guardrail_block(
                layer="layer2_tooling",
                kind="protected_branch",
                details=details,
                project=self.repo.split("/", 1)[-1] if "/" in self.repo else self.repo,
            )
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
        self._check_not_protected(name, operation="create_branch")
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
        self._check_not_protected(name, operation="delete_branch")
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
        self._check_not_protected(branch, operation="update_file")
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
            from minions.activity import record_guardrail_block

            record_guardrail_block(
                layer="layer2_tooling",
                kind="protected_branch",
                details=f"open_pull_request refused with head={head!r} in {self.repo}",
                project=self.repo.split("/", 1)[-1] if "/" in self.repo else self.repo,
            )
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

    def get_pr_failing_check_logs(
        self,
        number: int,
        *,
        max_bytes_per_check: int = 32_000,
        total_byte_cap: int = 128_000,
    ) -> list[FailingCheckLog]:
        """Return tail-truncated log excerpts for every failing check on a PR.

        Walks the check-runs on the PR's head SHA, filters to failed
        conclusions (``failure``/``timed_out``/``cancelled``/``action_required``),
        and pulls the actual log bytes:

        * GitHub Actions checks → fetch ``/actions/jobs/{job_id}/logs`` (job id
          parsed from ``details_url``). Returns plain text via a redirect to a
          signed URL.
        * Non-GHA checks (Vercel, CodeQL, security scanners, …) → use
          ``check_run.output.text`` if present, otherwise fall back to
          ``check_run.output.summary``.

        Bytes are tail-truncated to ``max_bytes_per_check`` per check (the
        failure is almost always at the bottom of the log). Iteration stops
        once cumulative bytes hit ``total_byte_cap`` so the engineer's context
        window stays bounded.

        Safe to call against any PR: stale head SHA / no checks / 403s all
        return ``[]`` (same swallow-and-continue pattern as
        ``get_pr_check_status``).
        """
        pr = self.get_pull_request(number)
        if not pr.head_sha:
            return []
        try:
            r = self._request("GET", f"/repos/{self.repo}/commits/{pr.head_sha}/check-runs")
        except GitHubError as e:
            if e.status_code in {403, 404, 422}:
                return []
            raise
        runs = r.json().get("check_runs", []) or []
        out: list[FailingCheckLog] = []
        total = 0
        for run in runs:
            if total >= total_byte_cap:
                break
            concl = (run.get("conclusion") or "").lower() or None
            if concl not in {"failure", "timed_out", "cancelled", "action_required"}:
                continue
            check_name = str(run.get("name") or "(unnamed check)")
            app = run.get("app") or {}
            app_slug = app.get("slug") if isinstance(app, dict) else None
            html_url = run.get("html_url") or run.get("details_url")
            details_url = run.get("details_url") or ""

            raw_log = ""
            if app_slug == "github-actions":
                job_id = _parse_gha_job_id(details_url)
                if job_id is not None:
                    raw_log = self._fetch_gha_job_logs(job_id)
            if not raw_log:
                output = run.get("output") or {}
                if isinstance(output, dict):
                    raw_log = str(output.get("text") or output.get("summary") or "")

            if not raw_log:
                # Nothing to show beyond the URL — surface a stub so the
                # engineer knows the check failed but logs were unavailable.
                raw_log = f"(no log content available; see {html_url})" if html_url else ""

            remaining_cap = total_byte_cap - total
            per_check_cap = min(max_bytes_per_check, remaining_cap)
            excerpt, truncated, original = _tail_truncate(raw_log, per_check_cap)

            out.append(
                FailingCheckLog(
                    check_name=check_name,
                    app_slug=app_slug,
                    conclusion=concl,
                    html_url=html_url,
                    log_excerpt=excerpt,
                    was_truncated=truncated,
                    original_bytes=original,
                )
            )
            total += len(excerpt.encode("utf-8"))
        return out

    def _fetch_gha_job_logs(self, job_id: int) -> str:
        """Fetch raw log text for a single GHA job.

        The GitHub logs endpoint 302s to a signed S3 URL with plain text;
        ``follow_redirects=True`` lets httpx chase it transparently. Returns
        an empty string on any failure — the engineer falls back to
        ``check_run.output`` content in that case.
        """
        try:
            response = self._client.request(
                "GET",
                f"/repos/{self.repo}/actions/jobs/{job_id}/logs",
                follow_redirects=True,
            )
        except httpx.RequestError:
            return ""
        if response.status_code >= 400:
            return ""
        return response.text or ""

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


_GHA_JOB_URL_RE = re.compile(r"/actions/runs/\d+/job/(\d+)")


def _parse_gha_job_id(details_url: str) -> int | None:
    """Extract the job id from a GHA check_run.details_url.

    The url looks like ``https://github.com/<owner>/<repo>/actions/runs/<run_id>/job/<job_id>``.
    Returns ``None`` when the pattern doesn't match (e.g. workflow_run-level
    check with no job id, or an unexpected URL shape).
    """
    if not details_url:
        return None
    m = _GHA_JOB_URL_RE.search(details_url)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def _tail_truncate(text: str, max_bytes: int) -> tuple[str, bool, int]:
    """Return ``(excerpt, was_truncated, original_bytes)``.

    Keeps the tail (last ``max_bytes`` UTF-8 bytes) because the meaningful
    failure line is almost always at the end of a CI log. Prepends a
    ``[truncated; original N KB]`` marker so the engineer knows there was
    more above. Slices on UTF-8 boundaries to avoid mid-codepoint cuts.
    """
    if max_bytes <= 0:
        return "", False, len(text.encode("utf-8"))
    encoded = text.encode("utf-8")
    original = len(encoded)
    if original <= max_bytes:
        return text, False, original
    tail = encoded[-max_bytes:].decode("utf-8", errors="ignore")
    marker = f"[truncated; original {original // 1000} KB, showing last {max_bytes // 1000} KB]\n"
    return marker + tail, True, original


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
