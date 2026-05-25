"""Budget-breach throttle (§6.5).

Reads the cost log written by ``cost.py`` and answers two questions:

  * What is project P's month-to-date cost vs its cap?
  * Should we run a (planning | engineer) action for P right now?

Three states:
  * ``ok``   — < 80% of monthly cap. Run normally.
  * ``warn`` — 80%–99% of monthly cap. Skip non-critical (planning,
                monitoring) sweeps. Still allow engineer execution of
                already-approved decisions, since that's operator-gated work.
  * ``breach`` — ≥ 100% of monthly cap. Refuse all LLM-spending actions.

Notification de-duplication: a tiny JSON file at
``data/local/budget_notifications.json`` records the (project, month, level)
tuples we've already alerted on, so the operator gets exactly one notification
per project per month per state transition.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from minions.cost import month_to_date_cost

if TYPE_CHECKING:
    from minions.models.manifest import Manifest
    from minions.notify.base import Notifier

logger = logging.getLogger(__name__)


WARN_THRESHOLD = 0.80   # 80% of monthly cap
BREACH_THRESHOLD = 1.00  # 100% of monthly cap

State = Literal["ok", "warn", "breach"]


@dataclass(frozen=True)
class BudgetState:
    project: str
    monthly_cap_usd: float
    month_to_date_usd: float
    fraction: float
    state: State

    @property
    def is_throttled(self) -> bool:
        """Pause non-critical work?"""
        return self.state in ("warn", "breach")

    @property
    def is_breached(self) -> bool:
        """Refuse all LLM spend?"""
        return self.state == "breach"


def evaluate(
    manifest: "Manifest",
    *,
    cost_log_path: Path | None = None,
    now: datetime | None = None,
) -> BudgetState:
    """Compute the current budget state for a project."""
    cap = manifest.monthly_budget_usd
    mtd = month_to_date_cost(manifest.name, now=now, path=cost_log_path)
    fraction = (mtd / cap) if cap > 0 else 0.0
    if fraction >= BREACH_THRESHOLD:
        state: State = "breach"
    elif fraction >= WARN_THRESHOLD:
        state = "warn"
    else:
        state = "ok"
    return BudgetState(
        project=manifest.name,
        monthly_cap_usd=cap,
        month_to_date_usd=mtd,
        fraction=fraction,
        state=state,
    )


# ---------------------------------------------------------------------------
# Notification de-dup.
# ---------------------------------------------------------------------------


def _read_notifications(path: Path) -> dict[str, list[str]]:
    """Map of "YYYY-MM" → list of "project:state" already notified."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): list(v) for k, v in data.items()}


def _write_notifications(path: Path, data: dict[str, list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def _month_key(now: datetime) -> str:
    return now.strftime("%Y-%m")


def maybe_notify(
    state: BudgetState,
    *,
    notifier: "Notifier",
    notifications_path: Path,
    now: datetime | None = None,
) -> bool:
    """Send a one-shot text notification if we haven't already this month.

    Returns True if a notification was sent.
    """
    if state.state == "ok":
        return False
    now = now or datetime.now(tz=UTC)
    month = _month_key(now)
    key = f"{state.project}:{state.state}"
    seen = _read_notifications(notifications_path)
    if key in seen.get(month, []):
        return False

    subject = (
        f"[minions/budget] {state.state.upper()}: {state.project} "
        f"at {state.fraction * 100:.0f}% of monthly cap"
    )
    body = (
        f"{state.project} has used ${state.month_to_date_usd:.4f} of its "
        f"${state.monthly_cap_usd:.2f} monthly cap "
        f"({state.fraction * 100:.1f}%).\n\n"
        + (
            "The project will be SKIPPED in non-critical scheduled sweeps "
            "(planning, monitoring) for the rest of this month.\n\n"
            "Engineer crew runs against ALREADY-APPROVED decisions are "
            "still permitted (until 100%)."
            if state.state == "warn"
            else "ALL LLM-spending actions for this project are now refused "
            "for the rest of this month. To raise the cap, edit "
            f"projects/{state.project}.yaml monthly_budget_usd, then re-run."
        )
    )
    try:
        notifier.notify_text(subject=subject, body=body)
    except Exception as e:  # noqa: BLE001 — notification failure must not crash flow
        logger.warning("budget notification failed: %s", e)
        return False

    seen.setdefault(month, []).append(key)
    _write_notifications(notifications_path, seen)
    return True


# ---------------------------------------------------------------------------
# Decision helpers.
# ---------------------------------------------------------------------------


class BudgetBreachError(RuntimeError):
    """Raised when the engineer crew is asked to run for a breached project."""

    def __init__(self, state: BudgetState) -> None:
        super().__init__(
            f"{state.project} has breached its ${state.monthly_cap_usd:.2f}/mo cap "
            f"(${state.month_to_date_usd:.4f} = {state.fraction * 100:.1f}%)."
        )
        self.state = state


def assert_can_run_engineer(state: BudgetState) -> None:
    """Raise BudgetBreachError if the project is fully breached."""
    if state.is_breached:
        raise BudgetBreachError(state)
