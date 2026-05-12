"""QA Engineer crew — post-PR review focused on test coverage + edge cases.

Fires from the PR follow-up sweep once CI is green and the PR has not yet
been QA-reviewed. Uses the Haiku tier (cheapest) and posts its findings as
a PR comment so the operator and downstream agents can see them.

This is meta-review: it does NOT run tests itself (CI does). It reviews the
*scope* of testing against the decision's intent.
"""

from __future__ import annotations

import json
import logging
import re
import textwrap

from pydantic import BaseModel

from minions.activity import crew_run
from minions.agents.roster import build_named_agent
from minions.cost import clear_attribution, set_attribution
from minions.models.decision import Decision
from minions.models.roles import Role
from minions.observability import add_metadata, observe_crew

logger = logging.getLogger(__name__)


class QAReview(BaseModel):
    """QA agent's review of a merged-or-mergeable PR."""

    test_coverage_score: int  # 1–10, where 10 = thorough
    concerns: list[str]
    suggested_tests: list[str]


def render_pr_comment(review: QAReview) -> str:
    """Stable markdown block for the PR comment body."""
    lines = [
        "🧪 **QA Engineer review**",
        "",
        f"**Test-coverage score:** {review.test_coverage_score}/10",
    ]
    if review.concerns:
        lines += ["", "**Concerns:**"]
        lines += [f"- {c}" for c in review.concerns]
    if review.suggested_tests:
        lines += ["", "**Suggested test cases:**"]
        lines += [f"- {t}" for t in review.suggested_tests]
    if not review.concerns and not review.suggested_tests:
        lines += ["", "_No additional concerns; coverage looks appropriate to the change scope._"]
    return "\n".join(lines)


@observe_crew("qa_engineer")
def run_qa_review(
    decision: Decision,
    *,
    files_changed: list[str],
    api_key: str | None = None,
    output_override: QAReview | None = None,
) -> QAReview | None:
    """Return a QAReview for the given Decision + the files its engineer crew changed."""
    add_metadata(
        crew="qa_engineer",
        project=decision.project,
        decision_id=str(decision.id),
        files_changed_count=len(files_changed),
    )

    if output_override is not None:
        return output_override

    if api_key is None:
        return None

    set_attribution(
        project=decision.project,
        decision_id=str(decision.id),
        role="qa_engineer",
    )
    try:
        with crew_run(
            crew="qa_engineer",
            project=decision.project,
            agents=["qa_engineer"],
            decision_id=str(decision.id),
        ):
            return _llm_review(decision, files_changed, api_key)
    finally:
        clear_attribution()


def _llm_review(decision: Decision, files_changed: list[str], api_key: str) -> QAReview | None:
    from crewai import Crew, Process, Task

    from minions.crews.factory import make_crewai_agent

    qa_min = build_named_agent(
        Role.QA_ENGINEER,
        project=decision.project,
        cadence="v0_frugal",
    )
    qa = make_crewai_agent(qa_min, api_key=api_key)

    description = textwrap.dedent(
        f"""\
        You are the QA Engineer. CI just passed on a PR that implements the
        decision below. Review the *scope of testing* against the decision's
        intent — do NOT re-run the tests, CI already did. Identify gaps.

        ## Decision
        Project:   {decision.project}
        Type:      {decision.type.value}
        Risk:      {decision.risk}
        Summary:   {decision.summary}

        Plan:
        {decision.diff_or_plan or '(none)'}

        ## Files changed in the PR
        {chr(10).join(f"- {f}" for f in files_changed) or "(none)"}

        ## Your task
        Produce a QAReview:
          - test_coverage_score: integer 1-10. 10 = the change is well covered
            by automated tests (existing or added in the PR). 1 = no coverage.
          - concerns: 0-5 specific gaps you spot (e.g., "POST /api/circuit/run
            rate-limit only verified for happy path; no test for the 429
            response", "Migration script has no rollback test").
          - suggested_tests: 0-5 concrete test cases worth adding next
            (one sentence each, specific to the changed files).

        Be concrete. Cite files by name. Empty lists are fine if coverage
        truly looks adequate — don't manufacture concerns.
        """
    )

    task = Task(
        description=description,
        agent=qa,
        expected_output=(
            "A QAReview with test_coverage_score (int 1-10), concerns "
            "(list[str], 0-5), suggested_tests (list[str], 0-5)."
        ),
        output_pydantic=QAReview,
    )
    crew = Crew(agents=[qa], tasks=[task], process=Process.sequential, verbose=False)
    result = crew.kickoff()

    pydantic_out = getattr(result, "pydantic", None)
    if isinstance(pydantic_out, QAReview):
        return pydantic_out
    return _parse_loose(str(result))


def _parse_loose(text: str) -> QAReview | None:
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    body = m.group(1) if m else text
    try:
        return QAReview.model_validate(json.loads(body))
    except (ValueError, json.JSONDecodeError):
        logger.warning("QA Engineer output unparseable: %r", text[:200])
        return None
