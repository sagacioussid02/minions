"""Dossier drafts — agent-maintained PROJECT_DOSSIER.md state per project.

A ``DossierDraft`` is the persistence record for a single produced dossier
markdown blob. The actual rendered markdown that ships to the target repo is
stored in ``markdown``; the structured metadata lives alongside.

Lifecycle:

    drafted --(operator approval)--> pr_open --(merge)--> merged
       \\
        --(operator reject)--> rejected
        --(newer draft lands first)--> superseded
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class DossierStatus(StrEnum):
    DRAFTED = "drafted"
    PR_OPEN = "pr_open"
    MERGED = "merged"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class DossierSection(StrEnum):
    ARCHITECTURE = "architecture"
    DATA = "data"
    INFRA = "infra"
    HOT_SPOTS = "hot_spots"
    TECH_DEBT = "tech_debt"
    SECURITY = "security"
    INCIDENTS = "incidents"
    QUESTIONS = "questions"


REQUIRED_SECTION_ORDER: tuple[DossierSection, ...] = (
    DossierSection.ARCHITECTURE,
    DossierSection.DATA,
    DossierSection.INFRA,
    DossierSection.SECURITY,
    DossierSection.HOT_SPOTS,
    DossierSection.TECH_DEBT,
    DossierSection.INCIDENTS,
    DossierSection.QUESTIONS,
)


class DossierDraft(BaseModel):
    """A single produced PROJECT_DOSSIER.md, in any lifecycle state."""

    id: UUID = Field(default_factory=uuid4)
    project: str
    commit_sha: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    status: DossierStatus = DossierStatus.DRAFTED

    pr_url: str | None = None
    pr_number: int | None = None
    merged_at: datetime | None = None

    markdown: str
    sections_present: list[DossierSection] = Field(default_factory=list)

    verifier_log: str | None = None
    crew_version: str = "discoverer/v1"


class DossierDigest(BaseModel):
    """Subset of a merged dossier passed into the planning prompt.

    Kept separate from ``DossierDraft`` so planning can be exercised against a
    cheap fixture without materialising the full markdown body.
    """

    project: str
    commit_sha: str
    generated_at: datetime
    freshness: str  # "ok" | "stale" | "very_stale"

    hot_spots_md: str = ""
    tech_debt_md: str = ""
    recent_incidents_md: str = ""
    open_questions_md: str = ""

    architecture_summary: str = ""
    data_summary: str = ""
    infra_summary: str = ""
