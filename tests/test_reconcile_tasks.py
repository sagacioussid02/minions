"""Tests for src/minions/tasks/reconcile.py — Task status from PR state."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx

from minions.github.client import GitHubClient
from minions.models.manifest import Manifest
from minions.models.task import Task
from minions.tasks.reconcile import reconcile_task_statuses
from minions.tasks.store import TaskStore

DECISION_ID = "00000000-0000-0000-0000-000000000001"


def _manifests(name: str = "demo_three", repo: str = "o/r") -> dict[str, Manifest]:
    m = Manifest.model_validate(
        {
            "name": name,
            "description": "test",
            "source": {"kind": "github", "path": "/tmp", "repo": repo, "default_branch": "main"},
            "weekly_budget_usd": 1.0,
            "monthly_budget_usd": 4.0,
            "owner": "o@o",
        }
    )
    return {name: m}


def _client_factory(
    handler: Callable[[httpx.Request], httpx.Response],
) -> Callable[[Manifest], GitHubClient]:
    def _open(manifest: Manifest) -> GitHubClient:
        return GitHubClient(token="x", repo="o/r", transport=httpx.MockTransport(handler))

    return _open


def _task(project: str, title: str, *, status: str, pr_number: int | None) -> Task:
    return Task(
        decision_id=DECISION_ID,  # type: ignore[arg-type]
        project=project,
        sprint_number=0,
        category="feature",
        title=title,
        description=f"Implement {title}",
        acceptance_criteria="ships",
        owner_role="engineer",
        owner_agent_id="engineer@demo_three",
        owner_display_name="Sasha",
        estimated_effort="m",
        status=status,  # type: ignore[arg-type]
        pr_number=pr_number,
        pr_url=f"https://github.com/o/r/pull/{pr_number}" if pr_number else None,
    )


def _pr_json(number: int, *, state: str, merged: bool) -> dict:
    return {
        "number": number,
        "title": "x",
        "body": "",
        "state": state,
        "head": {"ref": f"branch-{number}"},
        "base": {"ref": "main"},
        "draft": False,
        "html_url": "u",
        "merged": merged,
        "merged_at": "2026-05-04T22:50:00Z" if merged else None,
    }


def test_merged_pr_marks_task_done(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "tasks.json")
    t = _task("demo_three", "feat A", status="review", pr_number=1)
    store.save(t)

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_pr_json(1, state="closed", merged=True))

    changes = reconcile_task_statuses(
        task_store=store,
        manifests=_manifests("demo_three"),
        open_github_client=_client_factory(handler),
    )

    assert len(changes) == 1
    assert changes[0].after == "done"
    saved = store.get(t.id)
    assert saved is not None and saved.status == "done"
    assert saved.completed_at is not None


def test_closed_pr_marks_task_cancelled(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "tasks.json")
    t = _task("demo_three", "feat B", status="review", pr_number=2)
    store.save(t)

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_pr_json(2, state="closed", merged=False))

    changes = reconcile_task_statuses(
        task_store=store,
        manifests=_manifests("demo_three"),
        open_github_client=_client_factory(handler),
    )

    assert len(changes) == 1 and changes[0].after == "cancelled"
    assert store.get(t.id).status == "cancelled"  # type: ignore[union-attr]


def test_open_pr_leaves_task_in_review(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "tasks.json")
    t = _task("demo_three", "feat C", status="review", pr_number=3)
    store.save(t)

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_pr_json(3, state="open", merged=False))

    changes = reconcile_task_statuses(
        task_store=store,
        manifests=_manifests("demo_three"),
        open_github_client=_client_factory(handler),
    )

    assert changes == []
    assert store.get(t.id).status == "review"  # type: ignore[union-attr]


def test_idempotent_skips_terminal_and_prless_tasks(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "tasks.json")
    done = _task("demo_three", "already done", status="done", pr_number=4)
    no_pr = _task("demo_three", "no pr yet", status="queued", pr_number=None)
    store.save(done)
    store.save(no_pr)

    called = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        called["n"] += 1
        return httpx.Response(200, json=_pr_json(4, state="closed", merged=True))

    changes = reconcile_task_statuses(
        task_store=store,
        manifests=_manifests("demo_three"),
        open_github_client=_client_factory(handler),
    )

    assert changes == []
    assert called["n"] == 0  # neither task triggers a GitHub lookup


def test_unknown_or_nongithub_project_skipped(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "tasks.json")
    t = _task("ghost", "orphan", status="review", pr_number=5)
    store.save(t)

    def handler(req: httpx.Request) -> httpx.Response:
        raise AssertionError("should not be called for an unknown project")

    changes = reconcile_task_statuses(
        task_store=store,
        manifests=_manifests("demo_three"),  # 'ghost' not present
        open_github_client=_client_factory(handler),
    )

    assert changes == []
    assert store.get(t.id).status == "review"  # type: ignore[union-attr]
