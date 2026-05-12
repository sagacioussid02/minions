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


class BranchRef(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    sha: str
