"""Monthly portfolio review — the executive layer fires.

Four-stage sequential crew:

  1. CEO (Opus)  — strategic narrative for the next month
  2. CTO (Opus)  — tech / architecture priorities
  3. MD  (Opus)  — share-weight + budget re-allocation + sunset/revive
  4. Synth (Sonnet) — merge into a single PortfolioReview pydantic instance

Each stage receives the prior stage's output as plain context so the
synthesis is coherent. Devil's Advocate + Security Champion attach
afterward (via the standard ``risk>=medium`` hook in
``submit_for_approval``).

Cost discipline lives in **frequency** (monthly), not tier. The
executive layer should produce high-leverage output rarely.
"""

from __future__ import annotations

import json
import logging
import re
import textwrap
from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from minions.activity import crew_run
from minions.agents.roster import build_named_agent
from minions.cost import clear_attribution, set_attribution
from minions.models.decision import (
    Decision,
    DecisionStatus,
    DecisionType,
    PortfolioReview,
)
from minions.models.roles import Role
from minions.observability import add_metadata, observe_crew

if TYPE_CHECKING:
    from pathlib import Path

    from minions.approval.store_factory import DecisionStoreLike
    from minions.audit.store_factory import AuditFindingStoreLike
    from minions.config.portfolio import PortfolioConfig
    from minions.crews.engineer_runs_store_factory import EngineerRunStoreLike
    from minions.questions.store_factory import QuestionStoreLike

logger = logging.getLogger(__name__)


# =============================================================================
# Inputs the crew operates on
# =============================================================================


class ProjectMonthlyStats(BaseModel):
    """One row in the per-project digest the executive crew sees."""

    project: str
    decisions_30d: int = 0
    prs_merged_30d: int = 0
    cost_30d_usd: float = 0.0
    audit_findings_30d: int = 0
    open_questions: int = 0
    share_weight: float = 0.0
    monthly_cap_usd: float = 0.0
    cadence_profile: str = "v0_frugal"


class PortfolioReviewInputs(BaseModel):
    """Everything the executive crew needs to reason about a portfolio.

    Assembled (read-only) on the Python side before any LLM call by the
    Phase 2 assembler. Phase 1 ships this shape so tests can construct it
    explicitly.
    """

    portfolio_total_cost_30d_usd: float = 0.0
    portfolio_weekly_cap_usd: float = 50.0
    per_project: list[ProjectMonthlyStats] = Field(default_factory=list)
    deferred_projects: list[str] = Field(default_factory=list)
    current_period_label: str = ""  # e.g. "April 2026"


# =============================================================================
# Input assembly — pure-Python, no LLM
# =============================================================================


_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def _period_label(now: datetime) -> str:
    """`April 2026` formatting that the crew prompts embed."""
    return f"{_MONTHS[now.month - 1]} {now.year}"


def assemble_inputs(
    *,
    projects_dir: Path,
    decision_store: DecisionStoreLike,
    engineer_runs_store: EngineerRunStoreLike,
    audit_findings_store: AuditFindingStoreLike | None,
    questions_store: QuestionStoreLike | None,
    cost_log_path: Path | None = None,
    portfolio: PortfolioConfig | None = None,
    now: datetime | None = None,
    window_days: int = 30,
) -> PortfolioReviewInputs:
    """Build the digest the executive crew operates on.

    Pure-Python aggregation — no LLM. Reads every relevant store + the
    on-disk projects directory. Designed to be cheap (full scan over a
    30-day window is microseconds against in-memory or Postgres).
    """
    from datetime import UTC, datetime, timedelta

    from minions.cost import cost_by_project
    from minions.models.manifest import load_active_manifests

    now = now or datetime.now(tz=UTC)
    since = now - timedelta(days=window_days)
    manifests = load_active_manifests(projects_dir)
    cost_30d = cost_by_project(since=since, path=cost_log_path)

    # Per-project counters via single-pass scans.
    decisions_30d: dict[str, int] = {}
    for d in decision_store.list_all():
        if d.created_at < since:
            continue
        decisions_30d[d.project] = decisions_30d.get(d.project, 0) + 1

    prs_merged_30d: dict[str, int] = {}
    for r in engineer_runs_store.list_all():
        if r.merged_at is None or r.merged_at < since:
            continue
        prs_merged_30d[r.project] = prs_merged_30d.get(r.project, 0) + 1

    findings_30d: dict[str, int] = {}
    if audit_findings_store is not None:
        for f in audit_findings_store.list_all():
            if f.created_at < since:
                continue
            key = f.source_project or "<unattributed>"
            findings_30d[key] = findings_30d.get(key, 0) + 1

    open_questions: dict[str, int] = {}
    if questions_store is not None:
        for q in questions_store.list_all():
            if q.status.value not in ("open", "escalated"):
                continue
            open_questions[q.project] = open_questions.get(q.project, 0) + 1

    per_project: list[ProjectMonthlyStats] = []
    for name, manifest in sorted(manifests.items()):
        per_project.append(
            ProjectMonthlyStats(
                project=name,
                decisions_30d=decisions_30d.get(name, 0),
                prs_merged_30d=prs_merged_30d.get(name, 0),
                cost_30d_usd=round(cost_30d.get(name, 0.0), 4),
                audit_findings_30d=findings_30d.get(name, 0),
                open_questions=open_questions.get(name, 0),
                share_weight=manifest.delivery_targets.share_weight,
                monthly_cap_usd=manifest.monthly_budget_usd,
                cadence_profile=manifest.cadence_profile,
            )
        )

    deferred_dir = projects_dir.parent / projects_dir.name / "_deferred"
    deferred: list[str] = []
    if deferred_dir.is_dir():
        deferred = sorted(p.stem for p in deferred_dir.iterdir() if p.suffix in (".yaml", ".yml"))

    weekly_cap = 50.0
    if portfolio is not None and getattr(portfolio, "budget_envelope", None) is not None:
        envelope = portfolio.budget_envelope
        ceiling = getattr(envelope, "monthly_total_ceiling_usd", None)
        if ceiling:
            weekly_cap = round(ceiling / 4.345, 2)  # rough month → week

    return PortfolioReviewInputs(
        portfolio_total_cost_30d_usd=round(sum(cost_30d.values()), 4),
        portfolio_weekly_cap_usd=weekly_cap,
        per_project=per_project,
        deferred_projects=deferred,
        current_period_label=_period_label(now),
    )


# =============================================================================
# Public entrypoint
# =============================================================================


@observe_crew("portfolio_review")
def run_portfolio_review(
    *,
    inputs: PortfolioReviewInputs,
    api_key: str | None = None,
    portfolio: PortfolioConfig | None = None,
    dry_run: bool = False,
    output_override: PortfolioReview | None = None,
) -> Decision:
    """Run the four-stage executive crew and return a Decision ready to submit.

    The caller (``scheduled/monthly_portfolio_review.py``) is responsible
    for passing the Decision to ``submit_for_approval``. Returning the
    Decision (rather than persisting here) keeps this module pure-compute
    and matches the planning crew's contract.
    """
    add_metadata(
        crew="portfolio_review",
        period=inputs.current_period_label,
        projects_count=len(inputs.per_project),
    )

    # The portfolio review is cross-cutting — use a sentinel project name so
    # the activity / cost stores still get a stable key (NULL would lose us
    # per-row attribution).
    set_attribution(project="portfolio", decision_id="", role="ceo")
    try:
        with crew_run(
            crew="portfolio_review",
            project="portfolio",
            agents=["ceo", "cto", "md", "synthesis"],
            decision_id=None,
        ) as run_id:
            review = _produce_review(
                inputs=inputs,
                api_key=api_key,
                portfolio=portfolio,
                dry_run=dry_run,
                output_override=output_override,
            )
            if review is not None:
                from minions.transcripts.capture import record_crew_summary

                record_crew_summary(
                    run_id=run_id,
                    project="portfolio",
                    crew="portfolio_review",
                    agent_role="ceo",
                    result=review,
                    role_in_conversation="synthesis",
                )
    finally:
        clear_attribution()

    summary = _build_summary(review, inputs)
    rationale = (
        "Monthly executive portfolio review covering "
        f"{inputs.current_period_label or 'the prior 30 days'}. "
        "Generated by the CEO + CTO + MD + Synthesis crew."
    )
    return Decision(
        project="portfolio",  # cross-cutting; no single project owns it
        type=DecisionType.PORTFOLIO_REVIEW,
        summary=summary,
        rationale=rationale,
        diff_or_plan=_render_plan(review, inputs),
        risk="medium",  # triggers DA + Security
        proposer_role="ceo",
        proposer_agent_id="ceo@portfolio",
        proposer_display_name="Executive Crew",
        status=DecisionStatus.PENDING,
        portfolio_review=review,
    )


# =============================================================================
# Internals — stage chain
# =============================================================================


def _produce_review(
    *,
    inputs: PortfolioReviewInputs,
    api_key: str | None,
    portfolio: PortfolioConfig | None,
    dry_run: bool,
    output_override: PortfolioReview | None,
) -> PortfolioReview:
    if output_override is not None:
        return output_override
    if dry_run or api_key is None:
        return _dry_run_review(inputs)

    ceo_text = _run_stage(
        role=Role.CEO,
        api_key=api_key,
        portfolio=portfolio,
        description=_PROMPT_CEO.format(digest=_render_digest(inputs)),
    )
    cto_text = _run_stage(
        role=Role.CTO,
        api_key=api_key,
        portfolio=portfolio,
        description=_PROMPT_CTO.format(
            digest=_render_digest(inputs),
            ceo_narrative=ceo_text,
        ),
    )
    md_text = _run_stage(
        role=Role.MD,
        api_key=api_key,
        portfolio=portfolio,
        description=_PROMPT_MD.format(
            digest=_render_digest(inputs),
            ceo_narrative=ceo_text,
            cto_priorities=cto_text,
        ),
    )
    # Synthesis runs at Sonnet tier — use Role.MANAGER as a stand-in since
    # it is Sonnet by default. The synthesis prompt makes the role explicit
    # in the system message, so the model identity is unambiguous.
    synth_text = _run_stage(
        role=Role.MANAGER,
        api_key=api_key,
        portfolio=portfolio,
        description=_PROMPT_SYNTHESIS.format(
            digest=_render_digest(inputs),
            ceo_narrative=ceo_text,
            cto_priorities=cto_text,
            md_recommendations=md_text,
        ),
        expected_pydantic=PortfolioReview,
    )

    parsed = _parse_loose(synth_text)
    if parsed is not None:
        return parsed
    # Even the parse-loose pass failed — log and return the dry-run stub
    # so the operator still sees *something*, with confidence=1.
    logger.warning("portfolio_review synthesis unparseable; returning low-confidence stub")
    stub = _dry_run_review(inputs)
    stub.confidence = 1
    return stub


def _run_stage(
    *,
    role: Role,
    api_key: str,
    portfolio: PortfolioConfig | None,
    description: str,
    expected_pydantic: type | None = None,
) -> str:
    """Run one CrewAI Task and return its string output."""
    from crewai import Crew, Process, Task

    from minions.crews.factory import make_crewai_agent

    minion = build_named_agent(
        role,
        project=None,
        portfolio=portfolio,
        cadence="v0_frugal",
    )
    # Synthesis stage needs more output budget for the full pydantic JSON.
    max_tokens = 8192 if expected_pydantic is PortfolioReview else 2048
    agent = make_crewai_agent(minion, api_key=api_key, max_tokens=max_tokens)

    task_kwargs: dict[str, object] = {
        "description": description,
        "agent": agent,
        "expected_output": "a clear, concise response per the role's task description",
    }
    if expected_pydantic is not None:
        task_kwargs["output_pydantic"] = expected_pydantic
    task = Task(**task_kwargs)  # type: ignore[arg-type]
    crew = Crew(agents=[agent], tasks=[task], process=Process.sequential, verbose=False)
    result = crew.kickoff()

    if expected_pydantic is not None:
        pyd = getattr(result, "pydantic", None)
        if isinstance(pyd, expected_pydantic) and isinstance(pyd, BaseModel):
            # Re-serialize to canonical JSON so downstream parse is uniform.
            return pyd.model_dump_json()
    return str(result)


def _parse_loose(text: str) -> PortfolioReview | None:
    """Recover a PortfolioReview from fenced JSON when CrewAI's structured-output
    plumbing didn't give us a typed instance."""
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    body = m.group(1) if m else text
    try:
        return PortfolioReview.model_validate(json.loads(body))
    except (ValueError, json.JSONDecodeError):
        logger.warning("PortfolioReview output unparseable: %r", text[:200])
        return None


# =============================================================================
# Dry-run / formatting helpers
# =============================================================================


def _dry_run_review(inputs: PortfolioReviewInputs) -> PortfolioReview:
    return PortfolioReview(
        narrative=(
            "[DRY RUN] No LLM was invoked. A real run would synthesize a "
            "month-ahead strategic theme from the CEO + CTO + MD chain."
        ),
        tech_priorities=["[DRY RUN] tech priorities would be drafted here"],
        proposed_share_weight_changes={},
        proposed_budget_changes={},
        sunset_recommendations=[],
        revive_recommendations=[],
        confidence=3,
    )


def _render_digest(inputs: PortfolioReviewInputs) -> str:
    """Plain-text rollup of `inputs`, embedded into every stage's prompt."""
    lines: list[str] = []
    lines.append(f"## Portfolio digest — {inputs.current_period_label or 'last 30 days'}")
    lines.append(
        f"Total LLM spend (30d): ${inputs.portfolio_total_cost_30d_usd:.2f}; "
        f"weekly cap: ${inputs.portfolio_weekly_cap_usd:.2f}"
    )
    lines.append("")
    lines.append("### Per-project")
    for p in inputs.per_project:
        lines.append(
            f"- **{p.project}** — share_weight={p.share_weight:.2f}, "
            f"cap=${p.monthly_cap_usd:.2f}/mo, cadence={p.cadence_profile}, "
            f"decisions={p.decisions_30d}, PRs merged={p.prs_merged_30d}, "
            f"cost=${p.cost_30d_usd:.2f}, audit findings={p.audit_findings_30d}, "
            f"open questions={p.open_questions}"
        )
    if inputs.deferred_projects:
        lines.append("")
        lines.append(
            f"### Currently deferred (in projects/_deferred/): "
            f"{', '.join(inputs.deferred_projects)}"
        )
    return "\n".join(lines)


def _build_summary(review: PortfolioReview, inputs: PortfolioReviewInputs) -> str:
    """One-line headline that the operator sees in the Sprint Board card."""
    parts = [f"Portfolio review · {inputs.current_period_label or 'monthly'}"]
    changes = []
    if review.proposed_share_weight_changes:
        changes.append(f"{len(review.proposed_share_weight_changes)} weight change(s)")
    if review.proposed_budget_changes:
        changes.append(f"{len(review.proposed_budget_changes)} budget change(s)")
    if review.sunset_recommendations:
        changes.append(f"sunset {len(review.sunset_recommendations)}")
    if review.revive_recommendations:
        changes.append(f"revive {len(review.revive_recommendations)}")
    if changes:
        parts.append(" · ".join(changes))
    else:
        parts.append("no allocation changes recommended")
    return " — ".join(parts)


def _render_plan(review: PortfolioReview, inputs: PortfolioReviewInputs) -> str:
    """The Decision's `diff_or_plan` body — formatted markdown for the
    operator email + the Sprint Board detail drawer."""
    out: list[str] = []
    out.append(f"# Portfolio review — {inputs.current_period_label or 'monthly'}")
    out.append("")
    out.append("## Strategy (CEO)")
    out.append(review.narrative)
    out.append("")
    out.append("## Tech priorities (CTO)")
    for t in review.tech_priorities:
        out.append(f"- {t}")
    out.append("")
    out.append("## Allocation + budget (MD)")
    if review.proposed_share_weight_changes:
        out.append("**Share-weight changes:**")
        for proj, w in review.proposed_share_weight_changes.items():
            out.append(f"- `{proj}` → {w:.2f}")
    if review.proposed_budget_changes:
        out.append("**Monthly cap changes:**")
        for proj, cap in review.proposed_budget_changes.items():
            out.append(f"- `{proj}` → ${cap:.2f}/mo")
    if review.sunset_recommendations:
        out.append(f"**Sunset:** {', '.join(review.sunset_recommendations)}")
    if review.revive_recommendations:
        out.append(f"**Revive:** {', '.join(review.revive_recommendations)}")
    if not (
        review.proposed_share_weight_changes
        or review.proposed_budget_changes
        or review.sunset_recommendations
        or review.revive_recommendations
    ):
        out.append("_No allocation or budget changes recommended this month._")
    out.append("")
    out.append(f"_Crew confidence: {review.confidence}/5._")
    return "\n".join(out)


# =============================================================================
# Prompts — kept tight on purpose; the digest carries the data
# =============================================================================


_PROMPT_CEO = textwrap.dedent(
    """\
    You are the CEO of an autonomous AI engineering organization that
    owns a portfolio of software projects. Once a month you set the
    strategic theme for the following 30 days.

    {digest}

    ## Your task
    Read the digest and write **one paragraph** (≤120 words) naming the
    single most important strategic theme for the next month. Examples:
    "consolidate test coverage before adding any new features", or "tilt
    capacity toward Project X — it's seeing the most engagement". Be
    specific to the data above; do not produce generic advice.
    """
)

_PROMPT_CTO = textwrap.dedent(
    """\
    You are the CTO. The CEO has set the strategic theme below. Your job
    is to translate it into **2 to 4 specific technical directives** for
    the engineering line over the next 30 days.

    {digest}

    ## CEO narrative
    {ceo_narrative}

    ## Your task
    Output 2-4 bullets, each a concrete technical directive grounded in
    the digest. Examples: "Bring all 5 projects onto Node 20", "Pay down
    the dependency-freshness debt on Project Y before any new feature",
    "Add integration tests to Project Z — it has shipped 4 PRs without
    one". Be specific.
    """
)

_PROMPT_MD = textwrap.dedent(
    """\
    You are the Managing Director. The CEO has set the theme and the CTO
    has named tech priorities. Your job is the **allocation and budget**
    decision: which projects should get more capacity next month, which
    should get less, and which (if any) should be sunset or revived.

    {digest}

    ## CEO narrative
    {ceo_narrative}

    ## CTO priorities
    {cto_priorities}

    ## Your task
    Propose changes to (a) per-project share_weight (sums to a stable
    total — increases on one project must be balanced by decreases on
    others), (b) per-project monthly_cap_usd (operator hard ceiling is
    $50/wk portfolio total — do not exceed), (c) sunset recommendations,
    (d) revive recommendations (only from the currently-deferred list).

    If the digest does not justify any change, say "no change
    recommended" and explain in one sentence.

    Output: an English summary with explicit numbers where applicable.
    """
)

_PROMPT_SYNTHESIS = textwrap.dedent(
    """\
    You are the synthesis stage. The CEO, CTO, and MD have spoken. Your
    job is to package their output into a single, machine-readable
    PortfolioReview pydantic object.

    {digest}

    ## CEO narrative
    {ceo_narrative}

    ## CTO priorities
    {cto_priorities}

    ## MD recommendations
    {md_recommendations}

    ## Your task
    Produce a PortfolioReview JSON object with these fields:
      - narrative: copy the CEO's paragraph verbatim
      - tech_priorities: parse the CTO's bullets into a list of strings
      - proposed_share_weight_changes: map[project → new_weight] from MD
      - proposed_budget_changes: map[project → new_monthly_cap_usd] from MD
      - sunset_recommendations: list[project_name] from MD
      - revive_recommendations: list[project_name] from MD
      - confidence: integer 1-5 reflecting how grounded the three stages
        were in the digest (5 = every recommendation traceable to a
        specific number; 1 = mostly hand-waving)

    Empty lists / empty maps are valid when no change was recommended.

    Return JSON only, no surrounding prose.
    """
)
