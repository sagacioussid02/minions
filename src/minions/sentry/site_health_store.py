"""JSON-backed SiteHealthStore. Same 3-file pattern as QuestionStore.

Two responsibilities sit on the same store so a single migration covers
them and a single factory picks the backend:

* append-only ``site_health_samples`` for trend + uptime, and
* per-check ``site_alert_state`` for notifier dedup + recovery.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

AlertKind = Literal["down", "recovered"]


@dataclass(frozen=True)
class SiteHealthSample:
    """One probe outcome for a single (project, check_path)."""

    project: str
    check_path: str
    ts: datetime
    ok: bool
    status_code: int | None
    latency_ms: int | None
    error: str | None


@dataclass(frozen=True)
class AlertState:
    """The last alert this store emitted for (project, check_path)."""

    project: str
    check_path: str
    last_alert_at: datetime
    last_alert_kind: AlertKind


class SiteHealthStore:
    """JSON file at ``data/local/site_health.json``.

    Layout::

        {
          "samples": [ {project, check_path, ts, ok, status_code, latency_ms, error}, ... ],
          "alert_state": { "<project>::<check_path>": {last_alert_at, last_alert_kind}, ... }
        }
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text(json.dumps({"samples": [], "alert_state": {}}))

    # ---- internals ----

    def _load(self) -> dict[str, Any]:
        text = self.path.read_text()
        if not text.strip():
            return {"samples": [], "alert_state": {}}
        data: dict[str, Any] = json.loads(text)
        data.setdefault("samples", [])
        data.setdefault("alert_state", {})
        return data

    def _save(self, data: dict[str, Any]) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, default=str))
        tmp.replace(self.path)

    # ---- samples ----

    def record(self, sample: SiteHealthSample) -> None:
        data = self._load()
        data["samples"].append(
            {
                "project": sample.project,
                "check_path": sample.check_path,
                "ts": sample.ts.isoformat(),
                "ok": sample.ok,
                "status_code": sample.status_code,
                "latency_ms": sample.latency_ms,
                "error": sample.error,
            }
        )
        self._save(data)

    def list_recent_for_check(
        self,
        project: str,
        check_path: str,
        *,
        limit: int = 50,
    ) -> list[SiteHealthSample]:
        """Most-recent-first samples for one check. Used to compute the
        consecutive-failure / success streak that gates alerts."""
        data = self._load()
        rows = [
            r for r in data["samples"] if r["project"] == project and r["check_path"] == check_path
        ]
        rows.sort(key=lambda r: r["ts"], reverse=True)
        return [_sample_from_row(r) for r in rows[:limit]]

    def list_recent_for_project(self, project: str, *, limit: int = 500) -> list[SiteHealthSample]:
        data = self._load()
        rows = [r for r in data["samples"] if r["project"] == project]
        rows.sort(key=lambda r: r["ts"], reverse=True)
        return [_sample_from_row(r) for r in rows[:limit]]

    def list_all_samples(self) -> list[SiteHealthSample]:
        return [_sample_from_row(r) for r in self._load()["samples"]]

    # ---- alert state ----

    def get_alert_state(self, project: str, check_path: str) -> AlertState | None:
        data = self._load()
        key = _alert_key(project, check_path)
        raw = data["alert_state"].get(key)
        if raw is None:
            return None
        return AlertState(
            project=project,
            check_path=check_path,
            last_alert_at=datetime.fromisoformat(raw["last_alert_at"]),
            last_alert_kind=raw["last_alert_kind"],
        )

    def set_alert_state(self, state: AlertState) -> None:
        data = self._load()
        data["alert_state"][_alert_key(state.project, state.check_path)] = {
            "last_alert_at": state.last_alert_at.isoformat(),
            "last_alert_kind": state.last_alert_kind,
        }
        self._save(data)


def _alert_key(project: str, check_path: str) -> str:
    return f"{project}::{check_path}"


def _sample_from_row(row: dict[str, Any]) -> SiteHealthSample:
    return SiteHealthSample(
        project=row["project"],
        check_path=row["check_path"],
        ts=datetime.fromisoformat(row["ts"]),
        ok=bool(row["ok"]),
        status_code=row.get("status_code"),
        latency_ms=row.get("latency_ms"),
        error=row.get("error"),
    )
