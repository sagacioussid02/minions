"""Branch sweeper — garbage-collects stranded ``minions/eng/*`` branches.

When the engineer crew pushes commits but PR creation fails (PAT 403, 422
duplicate-PR, transient 5xx, network timeout), the rollback in
``crews/engineer.py`` deletes the branch in-process. This sweeper catches
what that misses: branches stranded by hard crashes, killed processes, or
old runs from before rollback existed.

**Safety guards** — a branch is deleted only when ALL of these hold:

  1. Name matches ``minions/eng/*`` (the engineer crew's namespace).
  2. Every commit on the branch carries a ``Minions-Run-Id:`` trailer
     (proves the orchestrator authored every commit). A single
     trailer-free commit means an operator touched the branch — leave
     it alone forever.
  3. The ``Minions-Run-Id`` value matches a known ``EngineerRunRecord``
     (or appears in ``activity_log`` as a ``crew_started`` event). This
     ties the branch back to the orchestrator's own bookkeeping.
  4. Tip commit is older than ``min_age_minutes`` (default 30 — protects
     active runs that haven't opened their PR yet).
  5. No open PR exists for the branch.

If any guard fails, the branch is **kept** and the reason is logged so the
operator can audit what the sweeper considered.

Schedule: hourly (`.github/workflows/branch_sweep.yml`).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from minions.activity import read_log
from minions.models.manifest import Manifest, load_active_manifests

if TYPE_CHECKING:
    from minions.github.client import GitHubClient

# Hard namespace contract — the engineer crew owns this prefix; operator
# branches go anywhere else.
BRANCH_PREFIX = "minions/eng/"

# Trailer that the engineer crew writes on every commit. Sweeper requires
# this on every commit on a branch before deletion.
_RUN_ID_TRAILER_RE = re.compile(r"^Minions-Run-Id:\s*(\S+)\s*$", re.MULTILINE)

SweepStatus = Literal[
    "deleted",
    "kept_no_trailer",
    "kept_unknown_run_id",
    "kept_too_young",
    "kept_open_pr",
    "kept_outside_namespace",
    "would_delete",
    "error",
]


class BranchOutcome(BaseModel):
    repo: str
    branch: str
    status: SweepStatus
    reason: str | None = None


class BranchSweepReport(BaseModel):
    started_at: str
    finished_at: str
    outcomes: list[BranchOutcome] = Field(default_factory=list)

    @property
    def deleted(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "deleted")

    @property
    def would_delete(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "would_delete")

    @property
    def kept(self) -> int:
        return sum(1 for o in self.outcomes if o.status.startswith("kept_"))

    @property
    def errored(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "error")


def _extract_run_ids(commits: list[dict]) -> tuple[bool, set[str]]:
    """Walk the commit list and return (all_trailered, set_of_run_ids).

    ``all_trailered`` is False if any commit lacks the trailer entirely;
    this is the "operator touched the branch" defense.
    """
    run_ids: set[str] = set()
    for c in commits:
        message = ((c.get("commit") or {}).get("message")) or ""
        m = _RUN_ID_TRAILER_RE.search(message)
        if m is None:
            return False, run_ids
        run_ids.add(m.group(1))
    return True, run_ids


def _tip_commit_at(commits: list[dict]) -> datetime | None:
    """ISO date of the newest commit on the branch, or None if missing."""
    if not commits:
        return None
    tip = commits[0]
    date_str = ((tip.get("commit") or {}).get("author") or {}).get("date")
    if not isinstance(date_str, str):
        return None
    try:
        # GitHub returns ISO 8601 with a trailing Z; tolerate both forms.
        return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except ValueError:
        return None


def run_branch_sweep(
    *,
    projects_dir: Path,
    open_github_client: Callable[[Manifest], GitHubClient | None],
    dry_run: bool = True,
    min_age_minutes: int = 30,
    known_run_ids: set[str] | None = None,
) -> BranchSweepReport:
    """Garbage-collect stranded engineer branches across managed repos.

    Iterates every project with a GitHub manifest. For each repo, lists
    branches matching ``BRANCH_PREFIX`` and evaluates the safety guards.
    Defaults to ``dry_run=True`` — produces a report of "would_delete"
    outcomes without touching anything. CLI promotes via ``--no-dry-run``.

    ``known_run_ids`` is the set of run_ids the orchestrator has emitted
    (via ``crew_run`` → activity_log). When None (default), derived from
    ``activity.read_log()`` — captures every crew that started, including
    ones that crashed before persisting an ``EngineerRunRecord``. Tests
    inject this directly.
    """
    started = datetime.now(tz=UTC).isoformat()

    if known_run_ids is None:
        known_run_ids = {e.run_id for e in read_log() if e.run_id}
    age_cutoff = datetime.now(tz=UTC) - timedelta(minutes=min_age_minutes)

    manifests = load_active_manifests(projects_dir)
    outcomes: list[BranchOutcome] = []

    for manifest in manifests.values():
        gh = open_github_client(manifest)
        if gh is None:
            continue  # non-GitHub-hosted project

        try:
            branches = gh.list_branches(prefix=BRANCH_PREFIX)
        except Exception as e:  # noqa: BLE001
            outcomes.append(
                BranchOutcome(
                    repo=str(getattr(manifest, "repo", manifest.name)),
                    branch="*",
                    status="error",
                    reason=f"list_branches failed: {str(e)[:120]}",
                )
            )
            continue

        for ref in branches:
            outcome = _evaluate_one(
                gh=gh,
                manifest=manifest,
                branch_name=ref.name,
                known_run_ids=known_run_ids,
                age_cutoff=age_cutoff,
                dry_run=dry_run,
            )
            outcomes.append(outcome)

    return BranchSweepReport(
        started_at=started,
        finished_at=datetime.now(tz=UTC).isoformat(),
        outcomes=outcomes,
    )


def _evaluate_one(
    *,
    gh: GitHubClient,
    manifest: Manifest,
    branch_name: str,
    known_run_ids: set[str],
    age_cutoff: datetime,
    dry_run: bool,
) -> BranchOutcome:
    repo = str(getattr(manifest, "repo", manifest.name))

    # Guard 1 — namespace (defensive; list_branches already filters)
    if not branch_name.startswith(BRANCH_PREFIX):
        return BranchOutcome(repo=repo, branch=branch_name, status="kept_outside_namespace")

    # Guard 5 — open PR check (fail fast; cheaper than fetching commits)
    try:
        pr = gh.find_pull_request_for_branch(branch=branch_name)
    except Exception as e:  # noqa: BLE001
        return BranchOutcome(
            repo=repo,
            branch=branch_name,
            status="error",
            reason=f"PR lookup failed: {str(e)[:120]}",
        )
    if pr is not None and (pr.state or "").lower() == "open":
        return BranchOutcome(
            repo=repo, branch=branch_name, status="kept_open_pr", reason=f"PR #{pr.number} open"
        )

    # Guards 2 + 3 + 4 — need the commit list
    try:
        commits = gh.list_branch_commits(branch=branch_name, limit=100)
    except Exception as e:  # noqa: BLE001
        return BranchOutcome(
            repo=repo,
            branch=branch_name,
            status="error",
            reason=f"commits fetch failed: {str(e)[:120]}",
        )

    all_trailered, run_ids = _extract_run_ids(commits)
    if not all_trailered:
        return BranchOutcome(
            repo=repo,
            branch=branch_name,
            status="kept_no_trailer",
            reason="one or more commits lack Minions-Run-Id trailer (operator may have edited)",
        )

    if not run_ids.issubset(known_run_ids):
        unknown = run_ids - known_run_ids
        return BranchOutcome(
            repo=repo,
            branch=branch_name,
            status="kept_unknown_run_id",
            reason=f"run_id(s) not in engineer_runs: {', '.join(sorted(unknown))[:80]}",
        )

    tip = _tip_commit_at(commits)
    if tip is not None and tip > age_cutoff:
        return BranchOutcome(
            repo=repo,
            branch=branch_name,
            status="kept_too_young",
            reason=f"tip at {tip.isoformat()}, < cutoff",
        )

    if dry_run:
        return BranchOutcome(repo=repo, branch=branch_name, status="would_delete")

    try:
        gh.delete_branch(name=branch_name)
    except Exception as e:  # noqa: BLE001
        return BranchOutcome(
            repo=repo, branch=branch_name, status="error", reason=f"delete failed: {str(e)[:120]}"
        )
    return BranchOutcome(repo=repo, branch=branch_name, status="deleted")
