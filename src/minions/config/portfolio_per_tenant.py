"""Load every tenant's project manifests from Postgres.

Mirrors ``models.manifest.load_active_manifests`` but reads ``tenant_projects``
(written by the web onboarding wizard) instead of projects/*.yaml on disk.
Each returned Manifest carries its owning ``tenant_id`` so downstream code
(GitHub auth, notify, activity logging) can branch on it.
"""

from __future__ import annotations

import json
import logging

from minions.db.connection import connect
from minions.models.manifest import Manifest

logger = logging.getLogger(__name__)


def load_tenant_manifests() -> dict[str, Manifest]:
    """Every tenant's manifests, keyed ``f"{tenant_id}:{project}"`` to avoid
    collisions between tenants (or with the founder's own project names).

    Skips (and logs) any row that fails Manifest validation instead of
    aborting the whole sweep — one bad tenant manifest shouldn't take down
    every other project's cron run.
    """
    manifests: dict[str, Manifest] = {}
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT tenant_id, project, manifest_json FROM tenant_projects")
        rows = cur.fetchall()

    for tenant_id, project, manifest_json in rows:
        try:
            raw = manifest_json if isinstance(manifest_json, dict) else json.loads(manifest_json)
            raw = {**raw, "tenant_id": str(tenant_id)}
            manifests[f"{tenant_id}:{project}"] = Manifest.model_validate(raw)
        except Exception as e:  # noqa: BLE001 — one bad tenant manifest shouldn't block others
            logger.warning("tenant_projects row %s/%s failed validation: %s", tenant_id, project, e)

    return manifests
