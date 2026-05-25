"""Post-deploy verification sweep.

For each active project with ``deploy.target != none``:

1. Find the most recently merged PR on the project's default branch.
2. Look up the matching Vercel deployment by sha.
3. Wait until it reaches a terminal state (READY / ERROR / CANCELED).
4. Run the deterministic health-check verifier.
5. If status is UNHEALTHY → file a ``risk=high`` revert Decision (deduped).
6. Persist the DeploymentRecord (idempotent by sha — re-runs UPDATE).

Failures are caught per-project. No LLM. Cost: zero Anthropic, a
handful of HTTP requests per project per tick.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from minions.approval.store import DecisionStore
from minions.deployments.revert import (
    file_revert_decision,
    find_open_revert_decision,
)
from minions.deployments.store_factory import DeploymentStoreLike
from minions.deployments.verifier import run_health_checks
from minions.models.deployment import (
    DeploymentRecord,
    DeploymentStatus,
)
from minions.models.manifest import Manifest, load_active_manifests
from minions.notify.base import Notifier

if TYPE_CHECKING:
    from minions.github.client import GitHubClient

logger = logging.getLogger(__name__)


class DeploymentOutcome(BaseModel):
    project: str
    status: Literal[
        "healthy",
        "unhealthy",
        "failed",
        "abandoned",
        "skipped",
        "error",
    ]
    merge_sha: str | None = None
    pr_number: int | None = None
    revert_decision_id: str | None = None
    failed_probes: int = 0
    total_probes: int = 0
    reason: str | None = None


class DeploymentSweepReport(BaseModel):
    started_at: str
    finished_at: str
    outcomes: list[DeploymentOutcome] = Field(default_factory=list)

    @property
    def unhealthy(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "unhealthy")

    @property
    def healthy(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "healthy")


def run_post_deploy_verify(
    *,
    projects_dir: Path,
    deployment_store: DeploymentStoreLike,
    decision_store: DecisionStore,
    notifier: Notifier,
    open_github_client: Callable[[Manifest], GitHubClient | None],
    dry_run: bool = True,
    projects: list[str] | None = None,
) -> DeploymentSweepReport:
    """Run the verifier sweep across active projects."""
    started = datetime.now(tz=UTC).isoformat()
    manifests = load_active_manifests(projects_dir)
    if projects:
        wanted = {p.lower() for p in projects}
        manifests = {n: m for n, m in manifests.items() if n.lower() in wanted}

    outcomes: list[DeploymentOutcome] = []
    for name, manifest in manifests.items():
        outcomes.append(
            _run_one(
                manifest=manifest,
                deployment_store=deployment_store,
                decision_store=decision_store,
                notifier=notifier,
                open_github_client=open_github_client,
                dry_run=dry_run,
            )
        )
        _ = name

    return DeploymentSweepReport(
        started_at=started,
        finished_at=datetime.now(tz=UTC).isoformat(),
        outcomes=outcomes,
    )


def _run_one(
    *,
    manifest: Manifest,
    deployment_store: DeploymentStoreLike,
    decision_store: DecisionStore,
    notifier: Notifier,
    open_github_client: Callable[[Manifest], GitHubClient | None],
    dry_run: bool,
) -> DeploymentOutcome:
    project = manifest.name
    cfg = manifest.deploy

    if cfg.target == "none":
        return DeploymentOutcome(
            project=project,
            status="skipped",
            reason="deploy.target=none",
        )
    if cfg.target != "vercel":
        # fly / render adapters not implemented yet (per the openspec out-of-scope).
        return DeploymentOutcome(
            project=project,
            status="skipped",
            reason=f"deploy.target={cfg.target!r} not supported yet",
        )
    if not cfg.production_url:
        return DeploymentOutcome(
            project=project,
            status="skipped",
            reason="no production_url configured",
        )
    if manifest.source.kind != "github" or not manifest.source.repo:
        return DeploymentOutcome(
            project=project,
            status="skipped",
            reason="non-github project — cannot resolve merge sha",
        )

    github = open_github_client(manifest)
    if github is None:
        return DeploymentOutcome(
            project=project,
            status="error",
            reason="failed to open GitHub client",
        )

    # Find the most-recent merged-to-main commit sha.
    try:
        with github:
            branch_ref = github.get_branch_ref(manifest.source.default_branch)
            merge_sha = branch_ref.sha
    except Exception as e:  # noqa: BLE001
        return DeploymentOutcome(
            project=project,
            status="error",
            reason=f"github head lookup failed: {e}",
        )

    # Idempotent: one row per (project, merge_sha). If we already verified
    # this sha, re-use that record so we never spam duplicate revert
    # Decisions.
    record = deployment_store.find_by_sha(project, merge_sha) or DeploymentRecord(
        project=project,
        merge_sha=merge_sha,
        deploy_target=cfg.target,
        production_url=cfg.production_url,
    )

    if record.status == DeploymentStatus.HEALTHY:
        return DeploymentOutcome(
            project=project,
            status="healthy",
            merge_sha=merge_sha,
            total_probes=len(record.health_check_results),
            reason="already verified healthy",
        )

    if dry_run:
        return DeploymentOutcome(
            project=project,
            status="skipped",
            merge_sha=merge_sha,
            reason="dry-run — would probe production_url",
        )

    # Run the verifier. (Vercel readiness polling deferred to a follow-up;
    # for v1 we probe production_url directly. If the deploy hasn't shipped
    # yet, probes will fail with 4xx and we'll retry on the next tick.)
    record.status = DeploymentStatus.CHECKING
    record = run_health_checks(config=cfg, record=record)
    with suppress(Exception):
        deployment_store.save(record)

    if record.status == DeploymentStatus.HEALTHY:
        return DeploymentOutcome(
            project=project,
            status="healthy",
            merge_sha=merge_sha,
            total_probes=len(record.health_check_results),
            failed_probes=0,
        )

    # Unhealthy — dedupe against existing revert decision for this sha.
    existing = find_open_revert_decision(
        project=project,
        merge_sha=merge_sha,
        decision_store=decision_store,
    )
    if existing is not None:
        return DeploymentOutcome(
            project=project,
            status="unhealthy",
            merge_sha=merge_sha,
            total_probes=len(record.health_check_results),
            failed_probes=record.failed_count,
            revert_decision_id=str(existing.id),
            reason="revert decision already pending/approved",
        )

    try:
        decision = file_revert_decision(
            record=record,
            decision_store=decision_store,
            notifier=notifier,
        )
        record.revert_decision_id = str(decision.id)
        with suppress(Exception):
            deployment_store.save(record)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "post-deploy: revert-decision filing failed for %s: %s",
            project,
            e,
            exc_info=True,
        )
        return DeploymentOutcome(
            project=project,
            status="error",
            merge_sha=merge_sha,
            total_probes=len(record.health_check_results),
            failed_probes=record.failed_count,
            reason=f"revert decision filing failed: {e}",
        )

    return DeploymentOutcome(
        project=project,
        status="unhealthy",
        merge_sha=merge_sha,
        total_probes=len(record.health_check_results),
        failed_probes=record.failed_count,
        revert_decision_id=record.revert_decision_id,
    )
