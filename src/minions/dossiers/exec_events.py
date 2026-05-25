"""Executive-layer learning events from dossier + backlog state changes.

Two emitters land here, both writing through ``learning/capture._save_unique``
so re-runs are idempotent:

* :func:`record_understanding_delta` — fires when a ``DossierDraft`` flips to
  ``merged``. Computes a section-level diff vs. the previously merged dossier
  (if any), emits one CEO event with a functional summary (added/removed
  features, hot spots, recent incidents) and one CTO event with a technical
  summary (architecture/infra/security/tech-debt deltas).
* :func:`record_backlog_proposed` — fires after a ``BacklogProposal`` Decision
  reaches APPROVED status. Emits one CTO event capturing how many issues
  were filed and the dossier they sourced from.

Both emitters return the saved learning records so callers can link them
back onto domain rows (``DossierDraft.ceo_learning_id`` /
``cto_learning_id``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from minions.dossiers.digest import parse_sections
from minions.learning.capture import MAX_FACT_CHARS, capture_enabled
from minions.models.dossier import DossierDraft
from minions.models.learning import AgentLearningRecord

if TYPE_CHECKING:
    from minions.learning.store_factory import AgentLearningStoreLike
    from minions.models.backlog import BacklogProposal
    from minions.models.decision import Decision


# Agent ids the executive seats read from on startup. Stays in lockstep with
# ``crews/portfolio_review.py`` and ``models/decision.py`` which use the same
# pseudo-agent ids for the CEO/CTO seats.
CEO_AGENT_ID = "ceo@portfolio"
CTO_AGENT_ID = "cto@portfolio"

# Topical source_type strings — kept here as constants so callers can search
# Postgres / JSONL stores for these events directly.
SOURCE_TYPE_UNDERSTANDING = "dossier_understanding_delta"
SOURCE_TYPE_BACKLOG = "dossier_backlog_proposed"


# ---------------------------------------------------------------------------
# Delta computation — pure, deterministic.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UnderstandingDelta:
    """Section-level diff of two dossier markdown bodies.

    Each ``*_changed`` flag is True when the section body text differs
    (whitespace-normalized) between prior and new — not just when a header
    appears/disappears. ``first_dossier=True`` means there is no prior to
    diff against.
    """

    project: str
    new_commit: str
    prior_commit: str | None
    first_dossier: bool
    architecture_changed: bool
    data_changed: bool
    infra_changed: bool
    hot_spots_changed: bool
    tech_debt_changed: bool
    security_changed: bool
    incidents_changed: bool
    questions_changed: bool

    @property
    def any_change(self) -> bool:
        return any(
            (
                self.architecture_changed,
                self.data_changed,
                self.infra_changed,
                self.hot_spots_changed,
                self.tech_debt_changed,
                self.security_changed,
                self.incidents_changed,
                self.questions_changed,
            )
        )

    @property
    def functional_changes(self) -> list[str]:
        """Sections the CEO cares about: user-facing surface area."""
        out: list[str] = []
        if self.hot_spots_changed:
            out.append("hot spots")
        if self.incidents_changed:
            out.append("recent incidents")
        if self.questions_changed:
            out.append("open questions")
        if self.data_changed:
            out.append("data flows")
        return out

    @property
    def technical_changes(self) -> list[str]:
        """Sections the CTO cares about: structural / operational surface."""
        out: list[str] = []
        if self.architecture_changed:
            out.append("architecture")
        if self.infra_changed:
            out.append("infra / deploy")
        if self.security_changed:
            out.append("security posture")
        if self.tech_debt_changed:
            out.append("tech-debt register")
        return out


def _norm(text: str) -> str:
    return " ".join(text.split()).strip()


def compute_understanding_delta(
    prior: DossierDraft | None, new: DossierDraft
) -> UnderstandingDelta:
    """Diff the markdown bodies of two dossiers, section by section.

    ``prior`` is None on first-ever discovery; every section is marked
    changed in that case so the executive layer gets a complete "here is
    what we now understand about this project" event.
    """
    new_sections = parse_sections(new.markdown)
    if prior is None:
        return UnderstandingDelta(
            project=new.project,
            new_commit=new.commit_sha,
            prior_commit=None,
            first_dossier=True,
            architecture_changed=bool(new_sections.get("architecture")),
            data_changed=bool(new_sections.get("data")),
            infra_changed=bool(new_sections.get("infra")),
            hot_spots_changed=bool(new_sections.get("hot_spots")),
            tech_debt_changed=bool(new_sections.get("tech_debt")),
            security_changed=bool(new_sections.get("security")),
            incidents_changed=bool(new_sections.get("incidents")),
            questions_changed=bool(new_sections.get("questions")),
        )
    prior_sections = parse_sections(prior.markdown)

    def diff(key: str) -> bool:
        return _norm(prior_sections.get(key, "")) != _norm(new_sections.get(key, ""))

    return UnderstandingDelta(
        project=new.project,
        new_commit=new.commit_sha,
        prior_commit=prior.commit_sha,
        first_dossier=False,
        architecture_changed=diff("architecture"),
        data_changed=diff("data"),
        infra_changed=diff("infra"),
        hot_spots_changed=diff("hot_spots"),
        tech_debt_changed=diff("tech_debt"),
        security_changed=diff("security"),
        incidents_changed=diff("incidents"),
        questions_changed=diff("questions"),
    )


# ---------------------------------------------------------------------------
# Fact rendering — short, audience-shaped sentences.
# ---------------------------------------------------------------------------


def _compact(text: str) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= MAX_FACT_CHARS:
        return normalized
    return normalized[: MAX_FACT_CHARS - 1].rstrip() + "..."


def _ceo_fact(delta: UnderstandingDelta) -> str:
    head = (
        f"First dossier on record for {delta.project}"
        if delta.first_dossier
        else f"Dossier refresh for {delta.project} "
        f"(commit {(delta.prior_commit or '?')[:8]} → {delta.new_commit[:8]})"
    )
    sections = delta.functional_changes
    tail = (
        f"functional sections updated: {', '.join(sections)}."
        if sections
        else "no user-facing sections changed (purely structural refresh)."
    )
    return _compact(f"{head}; {tail}")


def _cto_fact(delta: UnderstandingDelta) -> str:
    head = (
        f"First dossier on record for {delta.project}"
        if delta.first_dossier
        else f"Dossier refresh for {delta.project} "
        f"(commit {(delta.prior_commit or '?')[:8]} → {delta.new_commit[:8]})"
    )
    sections = delta.technical_changes
    tail = (
        f"technical sections updated: {', '.join(sections)}."
        if sections
        else "no architecture/infra/security/tech-debt sections changed."
    )
    return _compact(f"{head}; {tail}")


# ---------------------------------------------------------------------------
# Emitters — store-aware, idempotent via _save_unique.
# ---------------------------------------------------------------------------


def record_understanding_delta(
    *,
    prior: DossierDraft | None,
    new: DossierDraft,
    learning_store: AgentLearningStoreLike,
) -> tuple[AgentLearningRecord | None, AgentLearningRecord | None]:
    """Emit one CEO + one CTO learning event for a dossier merge.

    Returns ``(ceo_record, cto_record)``. Either may be ``None`` if learning
    capture is disabled or the dedupe pass detected a prior identical event.
    """
    if not capture_enabled():
        return (None, None)

    delta = compute_understanding_delta(prior, new)
    source_id = f"{new.project}:{new.commit_sha}"

    ceo = AgentLearningRecord(
        agent_id=CEO_AGENT_ID,
        role="ceo",
        project=new.project,
        kind="product",
        fact=_ceo_fact(delta),
        source_type=SOURCE_TYPE_UNDERSTANDING,
        source_id=source_id,
        confidence="medium",
    )
    cto = AgentLearningRecord(
        agent_id=CTO_AGENT_ID,
        role="cto",
        project=new.project,
        kind="technical",
        fact=_cto_fact(delta),
        source_type=SOURCE_TYPE_UNDERSTANDING,
        source_id=source_id,
        confidence="medium",
    )

    saved = _save_unique_pair(learning_store, [ceo, cto])
    ceo_out = saved[0] if saved else None
    cto_out = saved[1] if len(saved) > 1 else None
    return (ceo_out, cto_out)


def record_backlog_proposed(
    *,
    decision: Decision,
    proposal: BacklogProposal,
    learning_store: AgentLearningStoreLike,
    created_count: int | None = None,
) -> AgentLearningRecord | None:
    """Emit one CTO learning event for an approved backlog proposal.

    ``created_count`` is optional — when supplied (post-create), the fact
    reports created vs proposed; without it the fact reports proposed count.
    """
    if not capture_enabled():
        return None

    proposed = len(proposal.candidates)
    if created_count is None:
        counts = f"{proposed} candidate(s) proposed"
    else:
        counts = f"{created_count}/{proposed} created"
    kinds = sorted({c.kind.value for c in proposal.candidates})
    fact = _compact(
        f"Backlog proposal approved for {decision.project} "
        f"({counts}; kinds: {', '.join(kinds) or 'none'}; "
        f"dossier commit {proposal.dossier_commit_sha[:8]})."
    )
    record = AgentLearningRecord(
        agent_id=CTO_AGENT_ID,
        role="cto",
        project=decision.project,
        kind="process",
        fact=fact,
        source_type=SOURCE_TYPE_BACKLOG,
        source_id=str(decision.id),
        confidence="medium",
    )
    saved = _save_unique_pair(learning_store, [record])
    return saved[0] if saved else None


def _save_unique_pair(
    store: AgentLearningStoreLike,
    records: list[AgentLearningRecord],
) -> list[AgentLearningRecord]:
    """Same dedupe contract as ``learning.capture._save_unique`` — keys on
    ``(source_type, source_id, role, kind, fact)``. Kept local so this module
    has no learning-internal imports beyond the public store iface.
    """
    existing = {
        (r.source_type, r.source_id, r.role, r.kind, r.fact)
        for r in store.list_all(include_inactive=True)
    }
    out: list[AgentLearningRecord] = []
    for record in records:
        key = (record.source_type, record.source_id, record.role, record.kind, record.fact)
        if key in existing:
            continue
        out.append(store.save(record))
        existing.add(key)
    return out


__all__ = [
    "CEO_AGENT_ID",
    "CTO_AGENT_ID",
    "SOURCE_TYPE_BACKLOG",
    "SOURCE_TYPE_UNDERSTANDING",
    "UnderstandingDelta",
    "compute_understanding_delta",
    "record_backlog_proposed",
    "record_understanding_delta",
]
