"""Capacity + hiring models (openspec/changes/hire-as-decision).

A ``HireProposal`` is the structured block attached to a
``DecisionType.TEAM_COMPOSITION`` Decision's payload. The capacity watcher
(``scheduled/capacity_review.py``) produces these; the operator approves;
the engineer crew later patches the manifest (a follow-up phase).

The watcher is propose-only — it never mutates a roster. Every hire is a
Decision Record behind the operator's approval gate.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

HireLevel = Literal["intern", "standard", "senior"]
HireKind = Literal["hire"]  # "fire" / "promote" reserved for later


class HireEvidence(BaseModel):
    """Supporting metrics for a hire proposal. Plain numbers; the
    ``justification`` sentence on the proposal is the operator-facing summary."""

    seat_id: str  # the overloaded / understaffed seat, e.g. "senior_engineer@shared"
    current_load: int  # open tasks on the worst seat right now
    cap: int  # WIP cap that load is measured against
    unassigned_count: int  # role-matched tasks sitting unassigned
    projects_affected: list[str] = Field(default_factory=list)


class HireProposal(BaseModel):
    """Structured hire request carried on a TEAM_COMPOSITION Decision payload."""

    kind: HireKind = "hire"
    role: str  # any Role value from minions/models/roles.py
    level: HireLevel
    scope: str  # "shared" or a project name
    suggested_seat_id: str  # e.g. "senior_engineer@shared#1"
    suggested_display_name: str | None = None
    justification: str  # one plain-English sentence — always present
    evidence: HireEvidence
    cost_estimate_weekly_usd: float = 0.0
    alternatives_considered: list[str] = Field(default_factory=list)
