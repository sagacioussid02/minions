"""Deployment store backend selector. Mirrors ``dossiers/store_factory.py``."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol

from minions.db.connection import has_database_url
from minions.models.deployment import DeploymentRecord, DeploymentStatus


class DeploymentStoreLike(Protocol):
    def save(self, record: DeploymentRecord) -> DeploymentRecord: ...
    def get(self, record_id: str) -> DeploymentRecord | None: ...
    def list_all(self) -> list[DeploymentRecord]: ...
    def find_by_sha(self, project: str, merge_sha: str) -> DeploymentRecord | None: ...
    def list_for_project(
        self,
        project: str,
        status: DeploymentStatus | None = None,
        limit: int = 100,
    ) -> list[DeploymentRecord]: ...


def make_deployment_store(json_path: Path) -> DeploymentStoreLike:
    backend = (os.environ.get("MINIONS_STORE_BACKEND") or "").lower()
    if backend == "postgres":
        from minions.deployments.store_postgres import PostgresDeploymentStore

        return PostgresDeploymentStore()
    if backend == "json":
        from minions.deployments.store import DeploymentStore

        return DeploymentStore(json_path)
    if has_database_url():
        from minions.deployments.store_postgres import PostgresDeploymentStore

        return PostgresDeploymentStore()
    from minions.deployments.store import DeploymentStore

    return DeploymentStore(json_path)
