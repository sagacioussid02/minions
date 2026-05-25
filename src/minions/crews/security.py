"""Security Champion — pre-approval security review on risk>=medium proposals.

Mirror of ``devils_advocate``: fires from ``submit_for_approval`` /
``weekly_planning`` on risk>=medium decisions, attaches a ``SecurityReview``
to the Decision before the operator sees it. Informational only in v0 (no
veto power), but surfaces in the approval email so the operator can decide.
"""

from __future__ import annotations

import json
import logging
import re
import textwrap
from typing import TYPE_CHECKING

from minions.activity import crew_run
from minions.agents.roster import build_named_agent
from minions.cost import clear_attribution, set_attribution
from minions.models.decision import Decision, SecurityReview
from minions.models.roles import Role
from minions.observability import add_metadata, observe_crew

if TYPE_CHECKING:
    from minions.config.portfolio import PortfolioConfig

logger = logging.getLogger(__name__)


_TRIGGER_RISKS: frozenset[str] = frozenset({"medium", "high"})


def should_review(decision: Decision) -> bool:
    return decision.risk in _TRIGGER_RISKS


def attach_review(
    decision: Decision,
    *,
    api_key: str | None = None,
    portfolio: "PortfolioConfig | None" = None,
    output_override: SecurityReview | None = None,
) -> SecurityReview | None:
    """Run Security Champion (if risk gate passes) and attach to ``decision.security_review``."""
    if not should_review(decision):
        return None

    if output_override is None and api_key is None:
        return None

    set_attribution(
        project=decision.project,
        decision_id=str(decision.id),
        role="security_champion",
    )
    try:
        with crew_run(
            crew="security_champion",
            project=decision.project,
            agents=["security_champion"],
            decision_id=str(decision.id),
        ):
            result = review(
                decision,
                api_key=api_key,
                portfolio=portfolio,
                output_override=output_override,
            )
    finally:
        clear_attribution()

    if result is not None:
        decision.security_review = result
    return result


@observe_crew("security_champion")
def review(
    decision: Decision,
    *,
    api_key: str | None = None,
    portfolio: "PortfolioConfig | None" = None,
    output_override: SecurityReview | None = None,
) -> SecurityReview | None:
    """Generate a SecurityReview for a proposed Decision."""
    add_metadata(
        crew="security_champion",
        project=decision.project,
        decision_id=str(decision.id),
        decision_risk=decision.risk,
        decision_type=decision.type.value,
    )

    if output_override is not None:
        return output_override

    if api_key is None:
        return None

    from crewai import Crew, Process, Task

    from minions.crews.factory import make_crewai_agent

    sc_min = build_named_agent(
        Role.SECURITY_CHAMPION,
        project=decision.project,
        portfolio=portfolio,
        cadence="v0_frugal",
    )
    sc = make_crewai_agent(sc_min, api_key=api_key)

    description = textwrap.dedent(
        f"""\
        You are the Security Champion. Review this proposal for security risks
        BEFORE the operator approves it.

        ## Proposal under review
        Project:   {decision.project}
        Type:      {decision.type.value}
        Risk:      {decision.risk}
        Proposer:  {decision.proposer_display_name or decision.proposer_agent_id}
        Summary:   {decision.summary}

        Rationale:
        {decision.rationale}

        Plan:
        {decision.diff_or_plan or '(none)'}

        ## Your task
        Produce a SecurityReview with three fields:
          - verdict: one of "pass" (no security concerns), "flag" (review needed
            but not blocking), or "block" (proposal should not ship as-is).
          - concerns: a list of 1-5 SPECIFIC security concerns
            (e.g., "Plaintext API key in env.example", "No CSRF protection on
            mutation endpoint"). Empty list when verdict="pass".
          - reasoning: a one-paragraph rationale that ties verdict + concerns
            to the OWASP-style threat surface most relevant to this project.

        Be concrete. Cite the proposal directly. Do not invent threats that
        aren't grounded in the plan. If unsure, prefer "flag" over "block".
        """
    )

    task = Task(
        description=description,
        agent=sc,
        expected_output=(
            'A SecurityReview with verdict ("pass"/"flag"/"block"), '
            "concerns (list[str], 0-5), reasoning (str)."
        ),
        output_pydantic=SecurityReview,
    )
    crew = Crew(agents=[sc], tasks=[task], process=Process.sequential, verbose=False)
    result = crew.kickoff()

    pydantic_out = getattr(result, "pydantic", None)
    if isinstance(pydantic_out, SecurityReview):
        return pydantic_out
    return _parse_loose(str(result))


def _parse_loose(text: str) -> SecurityReview | None:
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    body = m.group(1) if m else text
    try:
        return SecurityReview.model_validate(json.loads(body))
    except (ValueError, json.JSONDecodeError):
        logger.warning("Security Champion output unparseable: %r", text[:200])
        return None
