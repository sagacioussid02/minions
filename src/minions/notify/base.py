"""Notifier protocol — every concrete notifier (console, Gmail) implements this."""

from __future__ import annotations

from typing import Protocol

from minions.models.decision import Decision


class Notifier(Protocol):
    """Sends approval requests to the operator."""

    def notify_approval_request(self, decision: Decision) -> None:
        """Notify the operator that a Decision is awaiting approval."""
        ...

    def notify_decision_resolved(self, decision: Decision) -> None:
        """Notify the operator (or the proposing agent) that a Decision was resolved."""
        ...

    def notify_text(self, *, subject: str, body: str) -> None:
        """Send an arbitrary informational message (digests, monitor reports).

        Distinct from approval requests — these never include action links and
        do not mutate Decision state.
        """
        ...
