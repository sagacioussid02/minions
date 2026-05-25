"""Crew heartbeat sweep — records lightweight availability check-ins.

This entrypoint does not run LLMs, does not open PRs, and does not call GitHub.
It simply emits activity events for the configured roster so the operator
console can distinguish "available and waiting" from "never seen".
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from minions.activity import ActivityEntry, append
from minions.agents.roster import (
    AUDIT,
    SHARED_EXECUTIVE,
    SHARED_SPECIALIST,
    project_role_slots,
)
from minions.models.manifest import load_active_manifests


class HeartbeatOutcome(BaseModel):
    scope: str
    project: str | None = None
    status: Literal["checked_in", "error"]
    roles: list[str] = Field(default_factory=list)
    error: str | None = None


class CrewHeartbeatReport(BaseModel):
    started_at: str
    finished_at: str
    outcomes: list[HeartbeatOutcome] = Field(default_factory=list)

    @property
    def checked_in(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "checked_in")

    @property
    def errored(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "error")


def run_crew_heartbeat(
    *,
    projects_dir: Path,
    activity_log_path: Path | None = None,
) -> CrewHeartbeatReport:
    """Record one availability check-in per project crew plus shared layers."""
    from datetime import UTC, datetime

    started_dt = datetime.now(tz=UTC)
    outcomes: list[HeartbeatOutcome] = []
    manifests = load_active_manifests(projects_dir)

    for name, manifest in sorted(manifests.items()):
        roles = _unique(role.value for role in project_role_slots(manifest))
        try:
            _append_checkin(
                project=name,
                scope=f"project:{name}",
                roles=roles,
                activity_log_path=activity_log_path,
            )
            outcomes.append(
                HeartbeatOutcome(
                    scope=f"project:{name}",
                    project=name,
                    status="checked_in",
                    roles=roles,
                )
            )
        except Exception as e:  # noqa: BLE001 — isolate one bad project
            outcomes.append(
                HeartbeatOutcome(
                    scope=f"project:{name}",
                    project=name,
                    status="error",
                    roles=roles,
                    error=str(e),
                )
            )

    shared_roles = _unique(role.value for role in [*SHARED_EXECUTIVE, *SHARED_SPECIALIST, *AUDIT])
    try:
        _append_checkin(
            project="",
            scope="shared",
            roles=shared_roles,
            activity_log_path=activity_log_path,
        )
        outcomes.append(
            HeartbeatOutcome(
                scope="shared",
                project=None,
                status="checked_in",
                roles=shared_roles,
            )
        )
    except Exception as e:  # noqa: BLE001
        outcomes.append(
            HeartbeatOutcome(
                scope="shared",
                project=None,
                status="error",
                roles=shared_roles,
                error=str(e),
            )
        )

    return CrewHeartbeatReport(
        started_at=started_dt.isoformat(),
        finished_at=datetime.now(tz=UTC).isoformat(),
        outcomes=outcomes,
    )


def _append_checkin(
    *,
    project: str,
    scope: str,
    roles: list[str],
    activity_log_path: Path | None,
) -> None:
    from datetime import UTC, datetime

    append(
        ActivityEntry(
            timestamp=datetime.now(tz=UTC),
            event="crew_checkin",
            run_id=f"heartbeat-{scope}-{uuid.uuid4().hex}",
            crew="crew_heartbeat",
            project=project,
            decision_id="",
            agents=tuple(roles),
        ),
        path=activity_log_path,
    )


def _unique(values: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out
