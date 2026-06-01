"""Devil's Advocate — produces the strongest counter-argument against a proposal.

Invoked synchronously by ``submit_for_approval`` for any Decision with
``risk >= medium``. The critique is attached to the Decision *before*
notification, so the operator sees the case against the proposal inline
in their email / console panel — not as a separate step.

Independence: the Devil's Advocate is part of the Audit & Challenge layer
(spec: ``audit-challenge``), reports directly to the operator, and cannot
be suppressed by the executive layer. In v0 it has no veto power — its
output is informational, attached to the Decision Record as ``critique``.

Cost discipline: only fires on risk≥medium. Most planning crew output is
``risk=low``, so this is sparing — typically 0–2 invocations/week.
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
from minions.models.decision import Decision, DevilsAdvocateCritique
from minions.models.roles import Role
from minions.observability import add_metadata, observe_crew

if TYPE_CHECKING:
    from minions.config.portfolio import PortfolioConfig

logger = logging.getLogger(__name__)


# Risks that trigger a Devil's Advocate critique. Spec calls for ≥ medium.
_TRIGGER_RISKS: frozenset[str] = frozenset({"medium", "high"})


def should_critique(decision: Decision) -> bool:
    """True if this decision warrants a Devil's Advocate run."""
    return decision.risk in _TRIGGER_RISKS


def attach_critique(
    decision: Decision,
    *,
    api_key: str | None = None,
    portfolio: PortfolioConfig | None = None,
    output_override: DevilsAdvocateCritique | None = None,
    memory_store: object | None = None,
) -> DevilsAdvocateCritique | None:
    """Run Devil's Advocate (if the risk gate passes) and attach to ``decision.critique``.

    No-op when:
      * decision.risk is "low" (cost discipline — most planning output is low)
      * api_key is None and no output_override (dry-run / unconfigured)

    Returns the attached critique, or ``None`` if skipped / failed.
    """
    if not should_critique(decision):
        return None

    if output_override is None and api_key is None:
        return None

    set_attribution(
        project=decision.project,
        decision_id=str(decision.id),
        role="devils_advocate",
    )
    try:
        with crew_run(
            crew="devils_advocate",
            project=decision.project,
            agents=["devils_advocate"],
            decision_id=str(decision.id),
        ) as run_id:
            result = critique(
                decision,
                api_key=api_key,
                portfolio=portfolio,
                output_override=output_override,
            )
            if result is not None:
                from minions.transcripts.capture import record_crew_summary

                record_crew_summary(
                    run_id=run_id,
                    project=decision.project,
                    crew="devils_advocate",
                    agent_role="devils_advocate",
                    result=result,
                    decision_id=str(decision.id),
                )
    finally:
        clear_attribution()

    if result is not None:
        decision.critique = result
        if memory_store is not None and hasattr(memory_store, "record"):
            memory_store.record(
                agent_id=f"devils_advocate@{decision.project}",
                sprint_number=decision.sprint_number,
                decision_id=decision.id,
                event="lesson_learned",
                summary=f"Flagged risk for '{decision.summary}': {result.counter_argument}",
                details="; ".join(result.failure_modes),
            )
    return result


@observe_crew("devils_advocate")
def critique(
    decision: Decision,
    *,
    api_key: str | None = None,
    portfolio: PortfolioConfig | None = None,
    output_override: DevilsAdvocateCritique | None = None,
) -> DevilsAdvocateCritique | None:
    """Generate a Devil's Advocate critique for a proposed Decision.

    Returns ``None`` when ``api_key`` is not provided (dry-run / unconfigured) or
    when the LLM output cannot be parsed. ``output_override`` short-circuits the
    LLM and uses the provided critique directly — for tests.
    """
    add_metadata(
        crew="devils_advocate",
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

    da_min = build_named_agent(
        Role.DEVILS_ADVOCATE,
        project=None,
        portfolio=portfolio,
        cadence="v0_frugal",
    )
    da = make_crewai_agent(da_min, api_key=api_key)

    description = textwrap.dedent(
        f"""\
        You are the Devil's Advocate. Your job is to produce the strongest
        counter-argument against an approved proposal so the operator sees
        the case AGAINST it before deciding.

        ## Proposal under review
        Project:   {decision.project}
        Type:      {decision.type.value}
        Risk:      {decision.risk}
        Proposer:  {decision.proposer_display_name or decision.proposer_agent_id}
        Summary:   {decision.summary}

        Rationale:
        {decision.rationale}

        Plan:
        {decision.diff_or_plan or "(none)"}

        ## Your task
        Produce a DevilsAdvocateCritique with three fields:
          - counter_argument: ONE strongest reason this should NOT proceed
            (one paragraph; cite the proposal directly).
          - failure_modes: 2-4 concrete ways this could go wrong in practice
            (specific, not generic — e.g., "X breaks Y in scenario Z").
          - alternative_considered: a more conservative or different approach
            the operator should weigh.

        Be skeptical but specific. No vague hand-waving. Reference the
        proposal text. If the proposal is sound, still produce the strongest
        counter — that's your role.
        """
    )

    task = Task(
        description=description,
        agent=da,
        expected_output=(
            "A DevilsAdvocateCritique with counter_argument (str), "
            "failure_modes (list[str], 2-4 items), alternative_considered (str)."
        ),
        output_pydantic=DevilsAdvocateCritique,
    )
    crew = Crew(agents=[da], tasks=[task], process=Process.sequential, verbose=False)
    result = crew.kickoff()

    pydantic_out = getattr(result, "pydantic", None)
    if isinstance(pydantic_out, DevilsAdvocateCritique):
        return pydantic_out
    return _parse_loose(str(result))


def _parse_loose(text: str) -> DevilsAdvocateCritique | None:
    """Best-effort fallback when the LLM doesn't return strict structured output."""
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    body = m.group(1) if m else text
    try:
        return DevilsAdvocateCritique.model_validate(json.loads(body))
    except (ValueError, json.JSONDecodeError):
        logger.warning("Devil's Advocate output unparseable: %r", text[:200])
        return None
