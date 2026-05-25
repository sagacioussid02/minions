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
    if cfg.target not in {"vercel", "generic"}:
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

    # Vercel readiness gate: when target=vercel and a token is available,
    # poll the Vercel API until the deployment matching this sha reaches a
    # terminal state. On ERROR/CANCELED we file the revert immediately
    # without bothering with HTTP probes. On token-missing or sha-not-found
    # we fall through to direct URL probing (same as generic).
    if cfg.target == "vercel":
        poll_outcome = _poll_vercel(record=record, project=project, merge_sha=merge_sha)
        if poll_outcome is not None:
            with suppress(Exception):
                deployment_store.save(record)
            if poll_outcome.status == "unhealthy":
                _file_revert_if_new(
                    record=record,
                    project=project,
                    merge_sha=merge_sha,
                    decision_store=decision_store,
                    notifier=notifier,
                    deployment_store=deployment_store,
                    outcome=poll_outcome,
                )
                _emit_learning(record=record, healthy=False)
            return poll_outcome

    record.status = DeploymentStatus.CHECKING
    record = run_health_checks(config=cfg, record=record)
    with suppress(Exception):
        deployment_store.save(record)

    if record.status == DeploymentStatus.HEALTHY:
        _emit_learning(record=record, healthy=True)
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

    _emit_learning(record=record, healthy=False)
    return DeploymentOutcome(
        project=project,
        status="unhealthy",
        merge_sha=merge_sha,
        total_probes=len(record.health_check_results),
        failed_probes=record.failed_count,
        revert_decision_id=record.revert_decision_id,
    )


def _poll_vercel(
    *,
    record: DeploymentRecord,
    project: str,
    merge_sha: str,
) -> DeploymentOutcome | None:
    """Poll Vercel for the deployment matching this sha.

    Returns:
      - None when polling cannot run (no token / sha not found) — caller
        falls through to direct URL probing.
      - DeploymentOutcome(status="unhealthy") when Vercel reports
        ERROR/CANCELED — caller files the revert without HTTP probes.
      - None when state reaches READY — caller proceeds to HTTP probes.
      - DeploymentOutcome(status="abandoned") on timeout.
    """
    from minions.deployments.vercel import (
        TERMINAL_STATES,
        find_deployment_by_sha,
        wait_until_terminal,
    )
    from minions.secrets import get_vercel_token

    token = get_vercel_token(project)
    if not token:
        logger.info("post-deploy: no VERCEL_TOKEN for %s — skipping readiness poll", project)
        return None
    handle = find_deployment_by_sha(token=token, sha=merge_sha)
    if handle is None:
        logger.info("post-deploy: vercel had no deployment for sha %s (%s)", merge_sha[:8], project)
        return None
    if handle.state not in TERMINAL_STATES:
        handle = wait_until_terminal(token=token, deployment_id=handle.id)
    if handle.state in {"ERROR", "CANCELED"}:
        record.status = DeploymentStatus.UNHEALTHY
        record.target_deploy_id = handle.id
        record.findings_md = (
            f"Vercel reported state={handle.state} for deployment {handle.id}; "
            "no HTTP probes were run (build did not reach READY)."
        )
        return DeploymentOutcome(
            project=project,
            status="unhealthy",
            merge_sha=merge_sha,
            reason=f"vercel state={handle.state}",
        )
    if handle.state != "READY":
        return DeploymentOutcome(
            project=project,
            status="abandoned",
            merge_sha=merge_sha,
            reason=f"vercel state={handle.state} after timeout",
        )
    return None


def _file_revert_if_new(
    *,
    record: DeploymentRecord,
    project: str,
    merge_sha: str,
    decision_store: DecisionStore,
    notifier: Notifier,
    deployment_store: DeploymentStoreLike,
    outcome: DeploymentOutcome,
) -> None:
    existing = find_open_revert_decision(
        project=project,
        merge_sha=merge_sha,
        decision_store=decision_store,
    )
    if existing is not None:
        outcome.revert_decision_id = str(existing.id)
        outcome.reason = "revert decision already pending/approved"
        return
    try:
        decision = file_revert_decision(
            record=record,
            decision_store=decision_store,
            notifier=notifier,
        )
        record.revert_decision_id = str(decision.id)
        outcome.revert_decision_id = str(decision.id)
        with suppress(Exception):
            deployment_store.save(record)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "post-deploy: revert-decision filing failed for %s: %s",
            project,
            e,
            exc_info=True,
        )


def _emit_learning(*, record: DeploymentRecord, healthy: bool) -> None:
    """Phase 6: tag deploy outcomes for CTO (failures) and CEO (stable)."""
    with suppress(Exception):
        from minions.db.connection import has_database_url
        from minions.learning.capture import capture_deploy_outcome
        from minions.learning.store import AgentLearningStore
        from minions.learning.store_postgres import PostgresAgentLearningStore

        store: AgentLearningStore | PostgresAgentLearningStore
        if has_database_url():
            store = PostgresAgentLearningStore()
        else:
            from minions.__main__ import AGENT_LEARNING_PATH

            store = AgentLearningStore(AGENT_LEARNING_PATH)
        capture_deploy_outcome(record=record, healthy=healthy, store=store)
