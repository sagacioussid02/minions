"""Backlog Proposal — agent-authored GitHub issue candidates with operator gate.

A ``BacklogProposal`` rides on a Decision Record (``DecisionType.OTHER`` with
kind ``backlog_proposal`` stamped on extras). Each candidate becomes a single
GitHub issue when the operator approves and the worker
(``minions backlog create``) runs.

The dedupe pass + cap are enforced at proposal time (so the operator sees an
already-filtered list); the worker re-runs dedupe at create time so that
issues filed during the approval window are still excluded.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class BacklogKind(StrEnum):
    FEATURE = "feature"
    BUG = "bug"
    TECH_DEBT = "tech_debt"
    SECURITY = "security"


_LABEL_BY_KIND: dict[BacklogKind, str] = {
    BacklogKind.FEATURE: "minions/feature",
    BacklogKind.BUG: "minions/bug",
    BacklogKind.TECH_DEBT: "minions/tech-debt",
    BacklogKind.SECURITY: "minions/security",
}


def label_for(kind: BacklogKind) -> str:
    """Canonical ``minions/*`` label for a backlog candidate kind."""
    return _LABEL_BY_KIND[kind]


class BacklogCandidate(BaseModel):
    """One proposed GitHub issue."""

    title: str
    body: str
    kind: BacklogKind
    source_section: str  # e.g. "tech_debt" / "open_questions" / "hot_spots"
    citations: list[str] = Field(
        default_factory=list,
        description="path:line anchors the candidate cites (used for dedupe).",
    )

    def label(self) -> str:
        return label_for(self.kind)


class BacklogProposal(BaseModel):
    """The full proposal carried on the Decision Record extras."""

    project: str
    dossier_commit_sha: str
    candidates: list[BacklogCandidate] = Field(default_factory=list)
