"""Branch sweeper safety-guard tests.

The sweeper must protect operator-touched branches and only delete
branches the engineer crew authored end-to-end. Tests fake the GitHub
client + the known-run-id set so they run offline.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from minions.github.models import BranchRef, PullRequest
from minions.models.manifest import load_active_manifests
from minions.scheduled.branch_sweep import BRANCH_PREFIX, run_branch_sweep

REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECTS_DIR = REPO_ROOT / "projects"


def _commit(message: str, *, days_ago: float = 5.0) -> dict[str, Any]:
    """Build a GitHub API-shaped commit dict for the fake client."""
    ts = (datetime.now(tz=UTC) - timedelta(days=days_ago)).isoformat()
    return {
        "sha": f"sha-{abs(hash(message)) % 100000}",
        "commit": {
            "message": message,
            "author": {"name": "Siddharth", "email": "x@y", "date": ts},
        },
    }


def _trailered(message: str, run_id: str = "deadbeef", decision_id: str = "abc-123") -> str:
    return (
        f"{message}\n\n"
        f"(role: engineer; decision: {decision_id})\n\n"
        f"Minions-Run-Id: {run_id}\n"
        f"Minions-Decision-Id: {decision_id}\n"
    )


class _FakeGithub:
    """Minimal GitHubClient stand-in for the sweeper's call surface."""

    def __init__(
        self,
        *,
        branches: list[BranchRef],
        commits_by_branch: dict[str, list[dict]],
        open_pr_branches: set[str] = frozenset(),
        repo: str = "owner/repo",
    ) -> None:
        self.branches = branches
        self.commits_by_branch = commits_by_branch
        self.open_pr_branches = set(open_pr_branches)
        self.repo = repo
        self.deleted: list[str] = []

    def list_branches(self, *, prefix: str | None = None) -> list[BranchRef]:
        if prefix is None:
            return list(self.branches)
        return [b for b in self.branches if b.name.startswith(prefix)]

    def list_branch_commits(self, *, branch: str, limit: int = 100) -> list[dict]:
        return list(self.commits_by_branch.get(branch, []))

    def find_pull_request_for_branch(self, *, branch: str) -> PullRequest | None:
        if branch in self.open_pr_branches:
            return PullRequest(
                number=99, html_url="https://example/pr/99", state="open",
                head=branch, base="main", draft=False, title="x", body=None, merged=False,
            )
        return None

    def delete_branch(self, *, name: str) -> None:
        self.deleted.append(name)


def _open_factory(gh: _FakeGithub):
    def _open(_manifest):
        return gh
    return _open


def _one_manifest_dir():
    """Return PROJECTS_DIR so the sweeper picks up real manifests; first one wins."""
    return PROJECTS_DIR


def test_kept_when_commit_lacks_trailer(tmp_path: Path) -> None:
    """Any commit without the trailer means the operator may have touched it."""
    branch = f"{BRANCH_PREFIX}some-work"
    gh = _FakeGithub(
        branches=[BranchRef(name=branch, sha="abc")],
        commits_by_branch={branch: [
            _commit(_trailered("first commit")),
            _commit("operator hand-edit, no trailer"),  # untrailered → kept
        ]},
    )
    report = run_branch_sweep(
        projects_dir=_one_manifest_dir(),
        open_github_client=_open_factory(gh),
        dry_run=False,
        known_run_ids={"deadbeef"},
    )
    statuses = [o.status for o in report.outcomes if o.branch == branch]
    assert "kept_no_trailer" in statuses
    assert gh.deleted == []


def test_kept_when_run_id_unknown(tmp_path: Path) -> None:
    """Trailered branch whose run_id is not in our records — leave alone."""
    branch = f"{BRANCH_PREFIX}foreign"
    gh = _FakeGithub(
        branches=[BranchRef(name=branch, sha="abc")],
        commits_by_branch={branch: [_commit(_trailered("c1", run_id="stranger"))]},
    )
    report = run_branch_sweep(
        projects_dir=_one_manifest_dir(),
        open_github_client=_open_factory(gh),
        dry_run=False,
        known_run_ids={"deadbeef"},  # "stranger" not in here
    )
    statuses = {o.status for o in report.outcomes if o.branch == branch}
    assert "kept_unknown_run_id" in statuses
    assert gh.deleted == []


def test_kept_when_open_pr_exists(tmp_path: Path) -> None:
    branch = f"{BRANCH_PREFIX}live"
    gh = _FakeGithub(
        branches=[BranchRef(name=branch, sha="abc")],
        commits_by_branch={branch: [_commit(_trailered("c1"))]},
        open_pr_branches={branch},
    )
    report = run_branch_sweep(
        projects_dir=_one_manifest_dir(),
        open_github_client=_open_factory(gh),
        dry_run=False,
        known_run_ids={"deadbeef"},
    )
    statuses = [o.status for o in report.outcomes if o.branch == branch]
    assert "kept_open_pr" in statuses
    assert gh.deleted == []


def test_kept_when_too_young(tmp_path: Path) -> None:
    branch = f"{BRANCH_PREFIX}fresh"
    gh = _FakeGithub(
        branches=[BranchRef(name=branch, sha="abc")],
        commits_by_branch={branch: [_commit(_trailered("c1"), days_ago=0.001)]},
    )
    report = run_branch_sweep(
        projects_dir=_one_manifest_dir(),
        open_github_client=_open_factory(gh),
        dry_run=False,
        known_run_ids={"deadbeef"},
        min_age_minutes=30,
    )
    statuses = [o.status for o in report.outcomes if o.branch == branch]
    assert "kept_too_young" in statuses
    assert gh.deleted == []


def test_deleted_when_all_guards_pass(tmp_path: Path) -> None:
    """Trailered + known run_id + no PR + old enough → delete."""
    branch = f"{BRANCH_PREFIX}stranded"
    gh = _FakeGithub(
        branches=[BranchRef(name=branch, sha="abc")],
        commits_by_branch={branch: [
            _commit(_trailered("c2"), days_ago=2),
            _commit(_trailered("c1"), days_ago=2),
        ]},
    )
    report = run_branch_sweep(
        projects_dir=_one_manifest_dir(),
        open_github_client=_open_factory(gh),
        dry_run=False,
        known_run_ids={"deadbeef"},
    )
    deleted = [o for o in report.outcomes if o.status == "deleted" and o.branch == branch]
    # One delete per managed manifest (fake client reused across all).
    assert len(deleted) >= 1
    assert all(b == branch for b in gh.deleted)
    assert len(gh.deleted) == len(deleted)


def test_dry_run_reports_would_delete_without_touching(tmp_path: Path) -> None:
    branch = f"{BRANCH_PREFIX}gc-candidate"
    gh = _FakeGithub(
        branches=[BranchRef(name=branch, sha="abc")],
        commits_by_branch={branch: [_commit(_trailered("c1"), days_ago=2)]},
    )
    report = run_branch_sweep(
        projects_dir=_one_manifest_dir(),
        open_github_client=_open_factory(gh),
        dry_run=True,
        known_run_ids={"deadbeef"},
    )
    would = [o for o in report.outcomes if o.status == "would_delete" and o.branch == branch]
    assert len(would) >= 1
    assert gh.deleted == []
