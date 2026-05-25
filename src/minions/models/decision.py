"""Decision Record — the unit of any material change in the org."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from minions.models.sprint_plan import StructuredSprintPlan


class DecisionType(StrEnum):
    FEATURE = "feature"
    BUG = "bug"
    DEP_UPGRADE = "dep_upgrade"
    INFRA = "infra"
    SECURITY = "security"
    COST = "cost"
    LICENSE = "license"
    TEAM_COMPOSITION = "team_composition"
    PROCUREMENT = "procurement"
    BUDGET_RAISE = "budget_raise"
    PORTFOLIO_REVIEW = "portfolio_review"
    DOSSIER_REFRESH = "dossier_refresh"
    OTHER = "other"


class DecisionStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    TIMED_OUT = "timed_out"
    EXECUTED = "executed"


RiskScore = Literal["low", "medium", "high"]
DecisionPriority = Literal["p1", "p2", "p3"]


# Role → default (priority, expedited) when a Decision is filed on behalf of a
# leadership role. Applied only when the caller leaves the defaults in place —
# explicit ``priority=...`` / ``expedited=...`` always wins. This is the
# autonomous answer to "if the CTO asks something, that must be P1": the role
# IS the signal, no LLM classifier required.
_ROLE_PRIORITY_DEFAULTS: dict[str, tuple[Literal["p1", "p2", "p3"], bool]] = {
    # p1 — executive / board layer. Their requests cut the queue.
    "ceo": ("p1", True),
    "cto": ("p1", True),
    "md": ("p1", True),
    "managing_director": ("p1", True),
    "chair": ("p1", True),
    "board": ("p1", True),
    "chief_product_officer": ("p1", True),
    "coo": ("p1", True),
    # p2 — operating layer. Expedited so they beat ordinary backlog but
    # do not steal slots from executives within the same sweep.
    "principal": ("p2", True),
    "principal_engineer": ("p2", True),
    "pm": ("p2", True),
    "product_manager": ("p2", True),
    "portfolio_owner": ("p2", True),
    "security_champion": ("p2", True),
    "spokesperson": ("p2", True),
    # Self-healing PR paths — also stamped explicitly at construction sites
    # in scheduled/pr_followup.py and scheduled/pr_review_loop.py; mirrored
    # here so this table is the single canonical reference.
    "pr_followup": ("p2", True),
    "pr_review_loop": ("p2", True),
}


def default_priority_for_role(
    role: str | None,
) -> tuple[Literal["p1", "p2", "p3"], bool] | None:
    """Lookup ``(priority, expedited)`` defaults for a leadership role.

    Returns ``None`` when the role is unknown or unset — caller should keep
    the model defaults (``p3``, not expedited) in that case.
    """
    if role is None:
        return None
    return _ROLE_PRIORITY_DEFAULTS.get(role.lower())


class DevilsAdvocateCritique(BaseModel):
    """Pre-approval critique attached to any Decision Record at risk >= medium."""

    counter_argument: str
    failure_modes: list[str]
    alternative_considered: str | None = None


SecurityVerdict = Literal["pass", "flag", "block"]


class SecurityReview(BaseModel):
    """Pre-approval security review attached to any Decision at risk >= medium.

    ``verdict`` is informational in v0 (no veto) — same model as Devil's Advocate.
    "block" surfaces in the operator's notification so they know to scrutinize.
    """

    verdict: SecurityVerdict
    concerns: list[str]
    reasoning: str


class PortfolioReview(BaseModel):
    """Monthly executive-layer review of the whole portfolio.

    Produced by the four-stage CEO → CTO → MD → Synthesis crew (see
    ``crews/portfolio_review.py``). Attached to a Decision of type
    ``PORTFOLIO_REVIEW`` at risk=medium so it triggers the standard
    Devil's Advocate + Security Champion hooks before reaching the
    operator's approval inbox.

    Field shape is deliberately loose — dict-typed where individual
    project keys vary across operators / portfolios. The post-approval
    YAML writer validates concrete keys against the live portfolio
    config at apply time.
    """

    narrative: str  # CEO — strategic theme for the next month
    tech_priorities: list[str]  # CTO — 2-4 directives
    proposed_share_weight_changes: dict[str, float] = Field(default_factory=dict)
    proposed_budget_changes: dict[str, float] = Field(default_factory=dict)
    sunset_recommendations: list[str] = Field(default_factory=list)
    revive_recommendations: list[str] = Field(default_factory=list)
    confidence: int = Field(ge=1, le=5)


class Decision(BaseModel):
    """A material change proposal. Always recorded; gated by operator approval
    unless it matches a project-specific auto-approval rule.
    """

    # ``extra="allow"`` preserves unknown payload keys round-trip through
    # ``model_validate`` -> ``model_dump``. Critical for cross-language
    # interop: the TS-side ``createSpikeDecision`` writes fields like
    # ``spike_source``, ``thread_id``, ``message_id``, ``question``,
    # ``consulted_roles`` directly to the Postgres payload. Without
    # ``extra="allow"`` those silently vanish the next time any Python
    # code touches the Decision (e.g., ``decisions priority`` mutating
    # priority + saving back), and the spokesperson relay loses the
    # thread linkage.
    model_config = ConfigDict(use_enum_values=False, extra="allow")

    id: UUID = Field(default_factory=uuid4)
    project: str
    type: DecisionType
    summary: str
    rationale: str
    diff_or_plan: str | None = None
    risk: RiskScore = "low"
    proposer_role: str
    proposer_agent_id: str
    # Human-readable name set at proposal time. Persisted on the record so renaming
    # an agent later doesn't rewrite history.
    proposer_display_name: str | None = None
    status: DecisionStatus = DecisionStatus.PENDING
    priority: DecisionPriority = "p3"
    expedited: bool = False
    requested_by_role: str | None = None

    # Per-project sprint counter (Phase 2 of openspec/sprint-tasks-memory).
    # NULL for Decisions created before sprint numbering shipped; bulk-fillable
    # via `minions sprints backfill <project>`.
    sprint_number: int | None = None

    critique: DevilsAdvocateCritique | None = None
    security_review: SecurityReview | None = None
    portfolio_review: PortfolioReview | None = None
    # Structured plan (Phase 1 of openspec/sprint-tasks-memory). Populated
    # by the planning crew; rendered to the legacy `diff_or_plan` markdown
    # for back-compat with email + existing UI surfaces.
    structured_plan: "StructuredSprintPlan | None" = None

    # Cost-coupled approvals (e.g., team_composition + budget_raise) link via this field.
    paired_decision_id: UUID | None = None

    pr_url: str | None = None
    base_sha: str | None = None  # SHA on main this proposal was based on, for replay/audit

    @model_validator(mode="after")
    def _apply_role_priority_defaults(self) -> Decision:
        """Auto-stamp priority/expedited from ``requested_by_role`` if untouched.

        Triggered only when both fields are still at their model defaults
        (``p3``, not expedited). An explicit ``priority="p3", expedited=False``
        from the caller is indistinguishable from "untouched" — that's the
        intentional trade-off, since downgrading a leadership ask to p3 is
        almost never what's meant.
        """
        if self.priority != "p3" or self.expedited:
            return self  # caller set something explicit — respect it
        defaults = default_priority_for_role(self.requested_by_role)
        if defaults is None:
            return self
        self.priority, self.expedited = defaults
        return self

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    resolved_at: datetime | None = None
    resolved_reason: str | None = None
