"""Lightweight Pydantic views over the GitHub REST API responses."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Repo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    full_name: str
    default_branch: str
    private: bool
    html_url: str


class Issue(BaseModel):
    model_config = ConfigDict(extra="ignore")

    number: int
    title: str
    body: str | None = None
    state: str
    labels: list[str] = []
    html_url: str
    user: str | None = None  # login


class PullRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    number: int
    title: str
    body: str | None = None
    state: str
    head: str  # branch name
    head_sha: str | None = None  # commit SHA of the PR head
    base: str
    draft: bool
    html_url: str
    merged: bool = False
    merged_at: str | None = None  # ISO 8601 string from GitHub
    closed_at: str | None = None
    mergeable_state: str | None = None


class BranchRef(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    sha: str


class FailingCheckLog(BaseModel):
    """Log excerpt for a single failing check_run on a PR's head SHA.

    Produced by ``GitHubClient.get_pr_failing_check_logs``. The engineer
    crew embeds these in the retry prompt so the owner agent can reason
    from concrete failure evidence instead of guessing from the diff.
    """

    model_config = ConfigDict(extra="ignore")

    check_name: str
    app_slug: str | None = None
    conclusion: str  # "failure", "timed_out", "cancelled", "action_required"
    html_url: str | None = None
    log_excerpt: str
    was_truncated: bool = False
    original_bytes: int = 0
