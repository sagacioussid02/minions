"""Tests for the engineer crew's failing-check log helpers and how they
render into the CI-fix-retry prompt mode block.

Pure helpers + a smoke test on the mode block string assembly. The full
LLM path is covered by `test_engineer_crew.py`; this file is scoped to
the Phase 2 hook into the GitHub log fetcher landed in Phase 1.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx

from minions.crews.engineer import (
    _gather_failing_check_logs,
    _render_failing_check_logs,
)
from minions.github.client import GitHubClient, GitHubError
from minions.github.models import FailingCheckLog


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> GitHubClient:
    return GitHubClient(token="ghp_test", repo="org/repo", transport=httpx.MockTransport(handler))


# ---------- _gather_failing_check_logs ----------


def test_gather_returns_logs_when_fetcher_succeeds():
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
                    "head": {"ref": "feat", "sha": "abc123"},
                    "base": {"ref": "main"},
                },
            )
        if "/check-runs" in req.url.path:
            return httpx.Response(
                200,
                json={
                    "check_runs": [
                        {
                            "name": "ruff",
                            "conclusion": "failure",
                            "app": {"slug": "github-actions"},
                            "details_url": "https://github.com/org/repo/actions/runs/1/job/2",
                            "output": {"text": "B008 do not use mutable default"},
                        }
                    ]
                },
            )
        if "/actions/jobs/2/logs" in req.url.path:
            return httpx.Response(200, text="full log\nB008 on file.py:42\n")
        return httpx.Response(500)

    logs = _gather_failing_check_logs(_client(handler), 7)
    assert len(logs) == 1
    assert logs[0].check_name == "ruff"
    assert "B008" in logs[0].log_excerpt


def test_gather_returns_empty_on_github_error():
    """Stale head SHA / 404 / 403 from the underlying client → empty list,
    never a raise. The retry must still proceed."""

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
                    "head": {"ref": "feat", "sha": "vanished"},
                    "base": {"ref": "main"},
                },
            )
        # check-runs endpoint returns 404 — head SHA disappeared.
        return httpx.Response(404, json={"message": "Not Found"})

    assert _gather_failing_check_logs(_client(handler), 7) == []


def test_gather_swallows_unexpected_exception(monkeypatch):
    """Any unanticipated error from the fetcher must not abort the retry."""

    class _Boom(GitHubClient):  # type: ignore[misc]
        def __init__(self) -> None:  # noqa: D401 — minimal stub
            pass

        def get_pr_failing_check_logs(self, *args, **kwargs):  # type: ignore[override]
            raise RuntimeError("disk on fire")

    assert _gather_failing_check_logs(_Boom(), 7) == []


def test_gather_swallows_github_error():
    """An explicit GitHubError from a 500-class response is caught."""

    class _BadClient(GitHubClient):  # type: ignore[misc]
        def __init__(self) -> None:
            pass

        def get_pr_failing_check_logs(self, *args, **kwargs):  # type: ignore[override]
            raise GitHubError("boom", status_code=500)

    assert _gather_failing_check_logs(_BadClient(), 7) == []


# ---------- _render_failing_check_logs ----------


def test_render_empty_logs_returns_diagnostic_stub():
    rendered = _render_failing_check_logs([])
    assert "no failing-check logs" in rendered
    # Engineer should still know to fall back to diff + criteria.
    assert "diagnose" in rendered.lower()


def test_render_single_log_emits_markdown_section():
    logs = [
        FailingCheckLog(
            check_name="pytest",
            app_slug="github-actions",
            conclusion="failure",
            html_url="https://github.com/org/repo/runs/42",
            log_excerpt="assertion failed at test_x.py:10\nE   AssertionError",
            was_truncated=False,
            original_bytes=80,
        )
    ]
    rendered = _render_failing_check_logs(logs)
    assert "### Failing check: `pytest`" in rendered
    assert "app=`github-actions`" in rendered
    assert "conclusion=`failure`" in rendered
    assert "[details](https://github.com/org/repo/runs/42)" in rendered
    assert "```text" in rendered
    assert "assertion failed at test_x.py:10" in rendered


def test_render_multiple_logs_separated_by_blank_lines():
    logs = [
        FailingCheckLog(
            check_name="ruff",
            app_slug="github-actions",
            conclusion="failure",
            html_url=None,
            log_excerpt="B008 bad default",
            was_truncated=False,
            original_bytes=15,
        ),
        FailingCheckLog(
            check_name="mypy",
            app_slug="github-actions",
            conclusion="failure",
            html_url=None,
            log_excerpt="error: incompatible types",
            was_truncated=False,
            original_bytes=24,
        ),
    ]
    rendered = _render_failing_check_logs(logs)
    assert rendered.count("### Failing check:") == 2
    # Each block ends with a closing fence; sections are separated by a
    # blank line.
    assert "```\n\n### Failing check: `mypy`" in rendered


def test_render_omits_optional_fields_when_missing():
    logs = [
        FailingCheckLog(
            check_name="UnknownScanner",
            app_slug=None,
            conclusion="failure",
            html_url=None,
            log_excerpt="(no log content available)",
            was_truncated=False,
            original_bytes=0,
        )
    ]
    rendered = _render_failing_check_logs(logs)
    assert "### Failing check: `UnknownScanner`" in rendered
    assert "app=" not in rendered  # omitted when None
    assert "[details]" not in rendered  # omitted when None
    assert "conclusion=`failure`" in rendered


def test_render_passes_truncation_marker_through_verbatim():
    """The `[truncated; original N KB]` marker stays in the prompt verbatim
    so the engineer knows there was more content above what they see."""
    logs = [
        FailingCheckLog(
            check_name="big-log",
            app_slug="github-actions",
            conclusion="failure",
            html_url=None,
            log_excerpt="[truncated; original 200 KB, showing last 32 KB]\nFAIL at end",
            was_truncated=True,
            original_bytes=200_000,
        )
    ]
    rendered = _render_failing_check_logs(logs)
    assert "[truncated; original 200 KB" in rendered
    assert "FAIL at end" in rendered
