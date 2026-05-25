"""Vercel adapter — find deployment by commit sha, poll until terminal.

Thin wrapper over the Vercel REST API. Used by the post-deploy
orchestrator to know WHEN to run health checks (only after the deploy
finishes building) and WHERE to point them (the deployment's
production URL).

Docs: https://vercel.com/docs/rest-api/endpoints/deployments
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Literal

import httpx

logger = logging.getLogger(__name__)

VercelState = Literal["BUILDING", "READY", "ERROR", "CANCELED", "QUEUED", "INITIALIZING", "UNKNOWN"]
TERMINAL_STATES: frozenset[str] = frozenset({"READY", "ERROR", "CANCELED"})

_API = "https://api.vercel.com"


@dataclass(frozen=True)
class VercelDeployment:
    id: str
    state: str  # see VercelState
    url: str  # default *.vercel.app preview/prod url
    inspector_url: str | None
    created_at: int  # ms since epoch
    target: str | None  # "production" | "preview" | None
    meta_sha: str | None  # commit sha from githubCommitSha


def find_deployment_by_sha(
    *,
    token: str,
    sha: str,
    team_id: str | None = None,
    limit: int = 20,
) -> VercelDeployment | None:
    """Most-recent deployment whose meta githubCommitSha matches ``sha``.

    Returns None when no match is found within ``limit`` recent rows.
    Caller should poll-then-give-up after ``max_wait_minutes``.
    """
    params: dict[str, str | int] = {"limit": limit}
    if team_id:
        params["teamId"] = team_id
    try:
        with httpx.Client(timeout=15.0) as c:
            r = c.get(
                f"{_API}/v6/deployments",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            r.raise_for_status()
            payload = r.json() or {}
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("vercel: list-deployments failed: %s", e)
        return None
    for row in payload.get("deployments") or []:
        meta = row.get("meta") or {}
        commit_sha = (
            meta.get("githubCommitSha")
            or meta.get("gitlabCommitSha")
            or meta.get("bitbucketCommitSha")
        )
        if commit_sha and commit_sha[:12] == sha[:12]:
            return _to_handle(row)
    return None


def wait_until_terminal(
    *,
    token: str,
    deployment_id: str,
    team_id: str | None = None,
    max_wait_seconds: int = 900,
    poll_seconds: float = 8.0,
) -> VercelDeployment:
    """Poll the deployment row until state is in TERMINAL_STATES.

    On timeout returns the last-observed handle (state probably still
    ``BUILDING``) — caller decides whether to mark the verification
    abandoned or to re-poll later.
    """
    params: dict[str, str | int] = {}
    if team_id:
        params["teamId"] = team_id
    deadline = time.monotonic() + max_wait_seconds
    last: VercelDeployment | None = None
    while time.monotonic() < deadline:
        try:
            with httpx.Client(timeout=15.0) as c:
                r = c.get(
                    f"{_API}/v13/deployments/{deployment_id}",
                    params=params,
                    headers={"Authorization": f"Bearer {token}"},
                )
                r.raise_for_status()
                row = r.json() or {}
        except (httpx.HTTPError, ValueError) as e:
            logger.warning("vercel: get-deployment failed: %s", e)
            time.sleep(poll_seconds)
            continue
        last = _to_handle(row)
        if last.state in TERMINAL_STATES:
            return last
        time.sleep(poll_seconds)
    if last is None:
        last = VercelDeployment(
            id=deployment_id,
            state="UNKNOWN",
            url="",
            inspector_url=None,
            created_at=0,
            target=None,
            meta_sha=None,
        )
    return last


def _to_handle(row: dict) -> VercelDeployment:  # type: ignore[type-arg]
    meta = row.get("meta") or {}
    return VercelDeployment(
        id=str(row.get("uid") or row.get("id") or ""),
        state=str(row.get("readyState") or row.get("state") or "UNKNOWN"),
        url=str(row.get("url") or ""),
        inspector_url=row.get("inspectorUrl") or None,
        created_at=int(row.get("created") or 0),
        target=row.get("target") or None,
        meta_sha=(
            meta.get("githubCommitSha")
            or meta.get("gitlabCommitSha")
            or meta.get("bitbucketCommitSha")
        ),
    )


__all__ = [
    "TERMINAL_STATES",
    "VercelDeployment",
    "find_deployment_by_sha",
    "wait_until_terminal",
]
