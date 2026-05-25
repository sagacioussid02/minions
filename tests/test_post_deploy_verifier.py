"""Minimal smoke for the post-deploy verifier (deterministic helpers)."""

from __future__ import annotations

from minions.deployments.verifier import _extract_image_urls
from minions.models.deployment import (
    DeploymentRecord,
    DeploymentStatus,
    HealthCheckResult,
)


def test_extract_image_urls_resolves_relative() -> None:
    html = (
        '<html><img src="/static/a.png"/>'
        '<img src="https://cdn.example.com/b.jpg"/>'
        '<img src="data:image/png;base64,xyz"/>'  # skipped
        '<img src="//assets.example.com/c.webp"/>'
        '</html>'
    )
    urls = _extract_image_urls(html, "https://example.com", max_n=5)
    assert urls == [
        "https://example.com/static/a.png",
        "https://cdn.example.com/b.jpg",
        "https://assets.example.com/c.webp",
    ]


def test_extract_image_urls_caps_max_n() -> None:
    html = "".join(f'<img src="/{i}.png"/>' for i in range(20))
    urls = _extract_image_urls(html, "https://example.com", max_n=3)
    assert len(urls) == 3


def test_deployment_record_count_helpers() -> None:
    rec = DeploymentRecord(
        project="p", merge_sha="abc", deploy_target="vercel",
        status=DeploymentStatus.UNHEALTHY,
        health_check_results=[
            HealthCheckResult(url="https://x/", kind="path",
                               actual_status=200, ok=True),
            HealthCheckResult(url="https://x/api", kind="path",
                               actual_status=500, ok=False,
                               error="HTTP 500"),
            HealthCheckResult(url="https://x/a.png", kind="image",
                               actual_status=400, ok=False,
                               error="HTTP 400"),
        ],
    )
    assert rec.healthy_count == 1
    assert rec.failed_count == 2
