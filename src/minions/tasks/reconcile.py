"""Reconcile Task status from the real PR state on GitHub.

A Task moves to ``review`` when its PR opens (see ``execute_approved``), but
nothing advanced it past that once the PR finally merged or closed. This sweep
closes that gap: for every non-terminal Task that carries a ``pr_number`` it
asks GitHub for the PR's state and marks the Task ``done`` (merged) or
``cancelled`` (closed without merge).

Driving from the Task's own ``pr_number`` — rather than the engineer-run
ledger, which is keyed by ``decision_id`` and keeps only the last run per
Decision — means every Task of a multi-task sprint proposal is covered.

Idempotent (terminal Tasks are skipped) and side-effect-light, so it is safe
to run on every monitor pass and doubles as the backfill for Tasks stranded in
``review`` from before this sweep existed.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING

from minions.models.task import TaskStatus
from minions.tasks.store_factory import TaskStoreLike

if TYPE_CHECKING:
    from minions.github.client import GitHubClient
    from minions.models.manifest import Manifest

_ALREADY_TERMINAL: frozenset[TaskStatus] = frozenset({"done", "cancelled"})


@dataclass(frozen=True)
class TaskStatusChange:
    task_id: str
    title: str
    project: str
    before: TaskStatus
    after: TaskStatus


def _terminal_status(*, merged: bool, state: str) -> TaskStatus | None:
    """Map a GitHub PR's state to the terminal Task status it implies."""
    if merged:
        return "done"
    if state == "closed":
        return "cancelled"
    return None


def reconcile_task_statuses(
    *,
    task_store: TaskStoreLike,
    manifests: dict[str, Manifest],
    open_github_client: Callable[[Manifest], GitHubClient | None],
    dry_run: bool = False,
) -> list[TaskStatusChange]:
    """Advance Tasks whose PR has merged/closed on GitHub. Returns the changes
    applied (or that *would* be applied when ``dry_run``)."""
    changes: list[TaskStatusChange] = []
    # One client per project, opened lazily and reused across that project's
    # Tasks. ``None`` means the project is local-only / has no GitHub client.
    clients: dict[str, GitHubClient | None] = {}

    for task in task_store.list_all():
        if task.status in _ALREADY_TERMINAL or task.pr_number is None:
            continue
        manifest = manifests.get(task.project)
        if manifest is None or manifest.source.kind != "github":
            continue
        if task.project not in clients:
            try:
                clients[task.project] = open_github_client(manifest)
            except Exception:  # noqa: BLE001 — a bad client must not abort the sweep
                clients[task.project] = None
        gh = clients[task.project]
        if gh is None:
            continue
        try:
            pr = gh.get_pull_request(task.pr_number)
        except Exception:  # noqa: BLE001 — transient API errors skip, don't crash
            continue
        target = _terminal_status(merged=pr.merged, state=pr.state)
        if target is None:
            continue
        changes.append(
            TaskStatusChange(
                task_id=str(task.id),
                title=task.title,
                project=task.project,
                before=task.status,
                after=target,
            )
        )
        if not dry_run:
            with suppress(Exception):
                task_store.update_status(task.id, target)

    for client in clients.values():
        if client is not None:
            with suppress(Exception):
                client.close()

    return changes
