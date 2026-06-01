"""Tests for ``GitHubClient.get_pr_failing_check_logs`` + tail-truncation helpers."""

from __future__ import annotations

from collections.abc import Callable

import httpx

from minions.github.client import (
    GitHubClient,
    _parse_gha_job_id,
    _tail_truncate,
)


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> GitHubClient:
    return GitHubClient(token="ghp_test", repo="org/repo", transport=httpx.MockTransport(handler))


def _pr_response(head_sha: str = "deadbeef") -> dict:
    return {
        "number": 7,
        "title": "x",
        "state": "open",
        "draft": True,
        "html_url": "u",
        "head": {"ref": "feat", "sha": head_sha},
        "base": {"ref": "main"},
    }


# ---------- _parse_gha_job_id ----------


def test_parse_gha_job_id_typical_url():
    url = "https://github.com/org/repo/actions/runs/123/job/456"
    assert _parse_gha_job_id(url) == 456


def test_parse_gha_job_id_with_query_string():
    url = "https://github.com/org/repo/actions/runs/123/job/789?check_suite_focus=true"
    assert _parse_gha_job_id(url) == 789


def test_parse_gha_job_id_returns_none_for_unrelated_url():
    assert _parse_gha_job_id("https://vercel.com/dashboard/deployments/xyz") is None
    assert _parse_gha_job_id("") is None
    assert _parse_gha_job_id("https://github.com/org/repo/actions/runs/123") is None


# ---------- _tail_truncate ----------


def test_tail_truncate_keeps_short_text_intact():
    text = "short log\nline 2\n"
    excerpt, truncated, original = _tail_truncate(text, max_bytes=1000)
    assert excerpt == text
    assert truncated is False
    assert original == len(text.encode("utf-8"))


def test_tail_truncate_keeps_tail_with_marker():
    text = "a" * 5000 + "FAIL_LINE\n"
    excerpt, truncated, original = _tail_truncate(text, max_bytes=100)
    assert truncated is True
    assert original == len(text.encode("utf-8"))
    assert "FAIL_LINE" in excerpt  # tail is preserved
    assert excerpt.startswith("[truncated;")  # marker on top
    # Marker + tail comfortably fits the 100-byte slice plus the marker prefix.
    assert excerpt.endswith("FAIL_LINE\n")


def test_tail_truncate_handles_utf8_boundary():
    # 1000 emojis (4 bytes each) followed by "END". Truncate to a cap that
    # lands mid-emoji on the byte level — must not raise.
    text = "🎉" * 1000 + "END"
    excerpt, truncated, _ = _tail_truncate(text, max_bytes=15)
    assert truncated is True
    assert "END" in excerpt


def test_tail_truncate_zero_or_negative_cap():
    excerpt, truncated, original = _tail_truncate("hello", max_bytes=0)
    assert excerpt == ""
    assert truncated is False
    assert original == 5


# ---------- get_pr_failing_check_logs ----------


def test_no_head_sha_returns_empty():
    def handler(req: httpx.Request) -> httpx.Response:
        body = _pr_response(head_sha="")
        body["head"]["sha"] = None  # type: ignore[index]
        return httpx.Response(200, json=body)

    assert _client(handler).get_pr_failing_check_logs(7) == []


def test_no_check_runs_returns_empty():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/pulls/7"):
            return httpx.Response(200, json=_pr_response())
        if "/check-runs" in req.url.path:
            return httpx.Response(200, json={"check_runs": []})
        return httpx.Response(500)

    assert _client(handler).get_pr_failing_check_logs(7) == []


def test_stale_head_sha_returns_empty_not_error():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/pulls/7"):
            return httpx.Response(200, json=_pr_response())
        if "/check-runs" in req.url.path:
            return httpx.Response(404, json={"message": "Not Found"})
        return httpx.Response(500)

    assert _client(handler).get_pr_failing_check_logs(7) == []


def test_only_failing_runs_are_returned():
    """Success/pending check-runs are filtered out."""

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/pulls/7"):
            return httpx.Response(200, json=_pr_response())
        if "/check-runs" in req.url.path:
            return httpx.Response(
                200,
                json={
                    "check_runs": [
                        {
                            "name": "lint",
                            "conclusion": "success",
                            "app": {"slug": "github-actions"},
                            "details_url": "",
                            "output": {"text": "all good"},
                        },
                        {
                            "name": "typecheck",
                            "conclusion": "failure",
                            "app": {"slug": "github-actions"},
                            "details_url": "https://github.com/org/repo/actions/runs/1/job/42",
                            "output": {"text": "mypy: type error"},
                            "html_url": "https://github.com/org/repo/runs/42",
                        },
                        {
                            "name": "deploy-preview",
                            "conclusion": None,  # in-flight
                            "status": "in_progress",
                            "app": {"slug": "vercel"},
                        },
                    ]
                },
            )
        if "/actions/jobs/42/logs" in req.url.path:
            return httpx.Response(200, text="mypy: error: incompatible types on line 3")
        return httpx.Response(500)

    logs = _client(handler).get_pr_failing_check_logs(7)
    assert len(logs) == 1
    assert logs[0].check_name == "typecheck"
    assert logs[0].conclusion == "failure"
    assert logs[0].app_slug == "github-actions"
    assert "mypy: error" in logs[0].log_excerpt


def test_gha_check_fetches_real_logs_over_output_text():
    """When the GHA logs endpoint returns content, prefer it over check_run.output.text."""

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/pulls/7"):
            return httpx.Response(200, json=_pr_response())
        if "/check-runs" in req.url.path:
            return httpx.Response(
                200,
                json={
                    "check_runs": [
                        {
                            "name": "tests",
                            "conclusion": "failure",
                            "app": {"slug": "github-actions"},
                            "details_url": "https://github.com/org/repo/actions/runs/1/job/100",
                            "output": {"text": "(short summary)", "summary": "(short)"},
                        }
                    ]
                },
            )
        if "/actions/jobs/100/logs" in req.url.path:
            return httpx.Response(200, text="full raw log\nassertion failed at test_x.py:42\n")
        return httpx.Response(500)

    logs = _client(handler).get_pr_failing_check_logs(7)
    assert "assertion failed at test_x.py:42" in logs[0].log_excerpt
    assert "(short summary)" not in logs[0].log_excerpt


def test_non_gha_check_uses_output_text():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/pulls/7"):
            return httpx.Response(200, json=_pr_response())
        if "/check-runs" in req.url.path:
            return httpx.Response(
                200,
                json={
                    "check_runs": [
                        {
                            "name": "Vercel Preview",
                            "conclusion": "failure",
                            "app": {"slug": "vercel"},
                            "details_url": "https://vercel.com/org/proj/abc",
                            "output": {
                                "text": "Build failed: missing env DATABASE_URL",
                                "summary": "Build error",
                            },
                        }
                    ]
                },
            )
        return httpx.Response(500)

    logs = _client(handler).get_pr_failing_check_logs(7)
    assert logs[0].log_excerpt == "Build failed: missing env DATABASE_URL"
    assert logs[0].app_slug == "vercel"


def test_non_gha_check_falls_back_to_output_summary():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/pulls/7"):
            return httpx.Response(200, json=_pr_response())
        if "/check-runs" in req.url.path:
            return httpx.Response(
                200,
                json={
                    "check_runs": [
                        {
                            "name": "CodeQL",
                            "conclusion": "failure",
                            "app": {"slug": "codeql"},
                            "details_url": "",
                            "output": {"text": "", "summary": "1 high-severity finding"},
                        }
                    ]
                },
            )
        return httpx.Response(500)

    logs = _client(handler).get_pr_failing_check_logs(7)
    assert logs[0].log_excerpt == "1 high-severity finding"


def test_no_log_content_anywhere_emits_stub():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/pulls/7"):
            return httpx.Response(200, json=_pr_response())
        if "/check-runs" in req.url.path:
            return httpx.Response(
                200,
                json={
                    "check_runs": [
                        {
                            "name": "Mystery scanner",
                            "conclusion": "failure",
                            "app": {"slug": "unknown"},
                            "details_url": "",
                            "html_url": "https://example.com/check/1",
                            "output": {},
                        }
                    ]
                },
            )
        return httpx.Response(500)

    logs = _client(handler).get_pr_failing_check_logs(7)
    assert logs[0].log_excerpt.startswith("(no log content available")
    assert "https://example.com/check/1" in logs[0].log_excerpt


def test_per_check_truncation_applied():
    huge = "a" * 80_000 + "TAIL_MARKER\n"

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/pulls/7"):
            return httpx.Response(200, json=_pr_response())
        if "/check-runs" in req.url.path:
            return httpx.Response(
                200,
                json={
                    "check_runs": [
                        {
                            "name": "big-log",
                            "conclusion": "failure",
                            "app": {"slug": "github-actions"},
                            "details_url": "https://github.com/org/repo/actions/runs/1/job/9",
                            "output": {},
                        }
                    ]
                },
            )
        if "/actions/jobs/9/logs" in req.url.path:
            return httpx.Response(200, text=huge)
        return httpx.Response(500)

    logs = _client(handler).get_pr_failing_check_logs(7, max_bytes_per_check=4000)
    assert logs[0].was_truncated is True
    assert logs[0].original_bytes == len(huge.encode("utf-8"))
    assert "TAIL_MARKER" in logs[0].log_excerpt
    assert logs[0].log_excerpt.startswith("[truncated;")


def test_total_byte_cap_stops_iteration():
    """A total cap across many failing checks halts the walk early."""
    big_payload = "x" * 50_000

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/pulls/7"):
            return httpx.Response(200, json=_pr_response())
        if "/check-runs" in req.url.path:
            return httpx.Response(
                200,
                json={
                    "check_runs": [
                        {
                            "name": f"check-{i}",
                            "conclusion": "failure",
                            "app": {"slug": "vercel"},
                            "details_url": "",
                            "output": {"text": big_payload},
                        }
                        for i in range(5)
                    ]
                },
            )
        return httpx.Response(500)

    logs = _client(handler).get_pr_failing_check_logs(
        7, max_bytes_per_check=20_000, total_byte_cap=50_000
    )
    # Total cap = 50k, per-check = 20k → we get at most 3 records before the
    # cumulative budget runs out (truncated to 20k, 20k, then 10k remaining).
    assert 2 <= len(logs) <= 3
    total = sum(len(log.log_excerpt.encode("utf-8")) for log in logs)
    assert total <= 50_000 + 200  # small slack for the truncation marker
