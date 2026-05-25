"""Post-deploy health checker.

Deterministic, no LLM. Walks the manifest's ``deploy.health_checks``
list, fetches each path against ``deploy.production_url``, captures
status + latency. When ``deploy.check_image_assets`` is True, also
fetches the first N ``<img src="…">`` URLs from the home-page HTML
and checks each returns 2xx — catches the next/image-optimizer
outage class (today's demo_five incident) without a headless
browser.

The verifier returns a populated ``DeploymentRecord``; the caller
decides what to do with it (persist, file revert decision, escalate).
"""

from __future__ import annotations

import logging
import re
import time
from datetime import UTC, datetime
from urllib.parse import urljoin, urlparse

import httpx

from minions.models.deployment import (
    DeploymentRecord,
    DeploymentStatus,
    HealthCheckResult,
)
from minions.models.manifest import DeployConfig

logger = logging.getLogger(__name__)

# Match <img src="…"> (and Next/Image's lazy-rendered <img loading="lazy">).
# Greedy enough for the home-page HTML pass; not a full HTML parser.
_IMG_SRC = re.compile(r"""<img[^>]+src=["']([^"']+)["']""", re.IGNORECASE)


def run_health_checks(
    *,
    config: DeployConfig,
    record: DeploymentRecord,
) -> DeploymentRecord:
    """Run every configured probe + (optionally) check image assets.

    Mutates ``record`` in place: appends ``HealthCheckResult`` entries,
    sets ``started_at`` / ``verified_at``, computes terminal status.
    Returns the record for caller convenience.
    """
    record.started_at = datetime.now(UTC)
    base = config.production_url
    if not base:
        record.status = DeploymentStatus.ABANDONED
        record.findings_md = (
            "No `production_url` configured for this project — verification skipped."
        )
        record.verified_at = datetime.now(UTC)
        return record

    results: list[HealthCheckResult] = []

    # 1. configured path probes
    if not config.health_checks:
        # Default to a single GET / so we at least catch hard-down sites.
        from minions.models.manifest import HealthCheck

        checks = [HealthCheck(path="/")]
    else:
        checks = config.health_checks

    for check in checks:
        url = urljoin(base.rstrip("/") + "/", check.path.lstrip("/"))
        result = _probe(
            url=url,
            expected_status=check.expect_status,
            expect_body_contains=check.expect_body_contains,
            timeout=check.timeout_seconds,
            kind="path",
        )
        results.append(result)

    # 2. image-asset probes (if any). Fetch home-page HTML, pull N <img>
    #    URLs, GET each. Skip if any of the path probes already failed
    #    (no point spamming a broken site).
    if config.check_image_assets and all(r.ok for r in results):
        home_url = base.rstrip("/") + "/"
        home_html = _fetch_text(home_url, timeout=10.0)
        if home_html is not None:
            urls = _extract_image_urls(home_html, base, max_n=config.max_image_assets)
            for img_url in urls:
                results.append(
                    _probe(
                        url=img_url,
                        expected_status=200,
                        expect_body_contains=None,
                        timeout=10.0,
                        kind="image",
                    )
                )

    record.health_check_results = results
    record.verified_at = datetime.now(UTC)
    record.status = (
        DeploymentStatus.HEALTHY if all(r.ok for r in results) else DeploymentStatus.UNHEALTHY
    )
    record.findings_md = _summarize_findings(results)
    return record


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _probe(
    *,
    url: str,
    expected_status: int,
    expect_body_contains: str | None,
    timeout: float,
    kind: str,
) -> HealthCheckResult:
    started = time.monotonic()
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as c:
            r = c.get(url)
        latency_ms = int((time.monotonic() - started) * 1000)
        ok = r.status_code == expected_status
        if ok and expect_body_contains:
            ok = expect_body_contains in r.text
        return HealthCheckResult(
            url=url,
            kind=kind,
            expected_status=expected_status,
            actual_status=r.status_code,
            latency_ms=latency_ms,
            error=None
            if ok
            else (
                f"expected {expected_status}, got {r.status_code}"
                if r.status_code != expected_status
                else f"body missing marker {expect_body_contains!r}"
            ),
            ok=ok,
        )
    except httpx.HTTPError as e:
        latency_ms = int((time.monotonic() - started) * 1000)
        return HealthCheckResult(
            url=url,
            kind=kind,
            expected_status=expected_status,
            actual_status=None,
            latency_ms=latency_ms,
            error=f"{type(e).__name__}: {e}",
            ok=False,
        )


def _fetch_text(url: str, *, timeout: float) -> str | None:
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as c:
            r = c.get(url)
        if r.status_code != 200:
            return None
        return r.text
    except httpx.HTTPError as e:
        logger.warning("verifier: home-page fetch failed for %s: %s", url, e)
        return None


def _extract_image_urls(html: str, base: str, *, max_n: int) -> list[str]:
    """Return up to max_n absolute image URLs from the page HTML.

    Skips data: URIs and tiny tracking pixels. Resolves relative paths
    against ``base``.
    """
    seen: list[str] = []
    for match in _IMG_SRC.finditer(html):
        src = match.group(1).strip()
        if not src or src.startswith("data:"):
            continue
        # Normalize to absolute URL.
        if src.startswith("//"):
            scheme = urlparse(base).scheme or "https"
            src = f"{scheme}:{src}"
        elif src.startswith("/"):
            src = urljoin(base.rstrip("/") + "/", src.lstrip("/"))
        elif not src.startswith(("http://", "https://")):
            src = urljoin(base.rstrip("/") + "/", src)
        if src in seen:
            continue
        seen.append(src)
        if len(seen) >= max_n:
            break
    return seen


def _summarize_findings(results: list[HealthCheckResult]) -> str:
    if not results:
        return "(no probes ran)"
    failed = [r for r in results if not r.ok]
    lines = [
        f"# Post-deploy verification — {len(results)} probe(s), {len(failed)} failed",
        "",
    ]
    for r in results:
        icon = "✓" if r.ok else "✗"
        latency = f"{r.latency_ms}ms" if r.latency_ms is not None else "?"
        status = f"HTTP {r.actual_status}" if r.actual_status is not None else "(no status)"
        line = f"- {icon} [{r.kind}] `{r.url}` — {status} in {latency}"
        if r.error:
            line += f" — **{r.error}**"
        lines.append(line)
    return "\n".join(lines)


__all__ = ["run_health_checks"]
