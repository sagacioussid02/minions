"""Persistence for ``AuditFinding`` objects.

JSON file at ``data/local/audit_findings.json`` keyed by finding id. Phase 6
swap: replace with the Neon Postgres ``audit_findings`` table.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from minions.models.audit import AuditFinding, FindingStatus


class AuditFindingStore:
    """JSON-file store for AuditFinding records."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _load_all(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save_all(self, data: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2, default=str))

    def save(self, finding: AuditFinding) -> AuditFinding:
        all_data = self._load_all()
        all_data[str(finding.id)] = json.loads(finding.model_dump_json())
        self._save_all(all_data)
        return finding

    def get(self, finding_id: str) -> AuditFinding | None:
        raw = self._load_all().get(finding_id)
        return AuditFinding.model_validate(raw) if raw else None

    def list_all(self) -> list[AuditFinding]:
        return [AuditFinding.model_validate(v) for v in self._load_all().values()]

    def list_open(self) -> list[AuditFinding]:
        return [f for f in self.list_all() if f.status == FindingStatus.OPEN]

    def list_by_pr_url(self, pr_url: str) -> list[AuditFinding]:
        return [f for f in self.list_all() if f.source_pr_url == pr_url]

    def has_finding_for_pr(self, pr_url: str) -> bool:
        """Check whether a PR already has at least one finding (skip re-audit)."""
        return bool(self.list_by_pr_url(pr_url))
