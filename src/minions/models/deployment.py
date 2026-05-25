"""Deployment verification records.

A ``DeploymentRecord`` is the persistence row for one post-merge
verification pass. One row per (project, sha) pair; re-running the
verifier on the same sha updates the existing row in place rather
than creating a duplicate.

Lifecycle (see ``openspec/changes/post-deploy-verification/``):

    pending → checking → healthy
                     ↘ unhealthy   (probe failed; revert decision filed)
                     ↘ failed      (deploy target reported build failure)
                     ↘ abandoned   (target=none or no deploy found in window)
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class DeploymentStatus(StrEnum):
    PENDING = "pending"
    CHECKING = "checking"
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    FAILED = "failed"
    ABANDONED = "abandoned"


class HealthCheckResult(BaseModel):
    """Outcome of one probe against the deployed site."""

    url: str
    kind: str  # "path" | "image" | other
    expected_status: int = 200
    actual_status: int | None = None
    latency_ms: int | None = None
    error: str | None = None
    ok: bool


class DeploymentRecord(BaseModel):
    """One post-merge verification pass for a single project sha."""

    id: UUID = Field(default_factory=uuid4)
    project: str
    pr_number: int | None = None
    merge_sha: str

    deploy_target: str  # "vercel" | "fly" | "render" | "none"
    target_deploy_id: str | None = None
    production_url: str | None = None

    detected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    verified_at: datetime | None = None

    status: DeploymentStatus = DeploymentStatus.PENDING
    health_check_results: list[HealthCheckResult] = Field(default_factory=list)

    revert_decision_id: str | None = None
    findings_md: str = ""

    @property
    def healthy_count(self) -> int:
        return sum(1 for r in self.health_check_results if r.ok)

    @property
    def failed_count(self) -> int:
        return sum(1 for r in self.health_check_results if not r.ok)
