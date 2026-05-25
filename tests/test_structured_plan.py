"""StructuredSprintPlan parsing + rendering tests.

Phase 1 of openspec/sprint-tasks-memory: the planning crew produces a
typed structured plan (JSON), parsed with retries + fallback. These tests
cover the parser and the back-compat markdown renderer that fills the
legacy `diff_or_plan` field.
"""

from __future__ import annotations

from minions.crews.planning import _parse_structured_plan
from minions.models.sprint_plan import PlanItem, StructuredSprintPlan


def test_render_markdown_includes_all_sections() -> None:
    plan = StructuredSprintPlan(
        goal="Ship audit log v2",
        features=[
            PlanItem(
                title="Search endpoint",
                rationale="ops asked",
                acceptance_criteria="paginates",
                estimated_effort="m",
                suggested_owner_role="engineer",
            )
        ],
        bugs=[PlanItem(title="Cron noise", estimated_effort="s")],
        ops=[PlanItem(title="Promote staging", estimated_effort="m")],
        risks=["Postgres FTS index migration is unfamiliar territory"],
    )
    md = plan.render_markdown()
    assert "Sprint goal" in md
    assert "Ship audit log v2" in md
    assert "Features (1)" in md
    assert "Bugs (1)" in md
    assert "Ops (1)" in md
    assert "Tech Debt" not in md  # empty section omitted
    assert "Docs" not in md
    assert "Risks" in md
    assert "Postgres FTS" in md
    # Effort + owner pills surface in the rendered line
    assert "[m]" in md
    assert "engineer" in md


def test_render_markdown_skips_empty_sections() -> None:
    plan = StructuredSprintPlan(goal="empty")
    md = plan.render_markdown()
    assert "Features" not in md
    assert "Risks" not in md
    assert md.strip().endswith("empty")


def test_parser_accepts_bare_json() -> None:
    text = '{"goal": "g", "features": [{"title": "t"}]}'
    plan = _parse_structured_plan(text, project="Demo")
    assert plan.goal == "g"
    assert plan.features[0].title == "t"


def test_parser_strips_markdown_fences() -> None:
    text = '```json\n{"goal": "g", "bugs": [{"title": "b"}]}\n```'
    plan = _parse_structured_plan(text, project="Demo")
    assert plan.goal == "g"
    assert plan.bugs[0].title == "b"


def test_parser_trims_trailing_prose() -> None:
    text = '{"goal": "g", "features": []}\n\nThat is my plan, hope you like it!'
    plan = _parse_structured_plan(text, project="Demo")
    assert plan.goal == "g"
    assert plan.features == []


def test_parser_falls_back_when_invalid() -> None:
    text = "totally not json, just some prose about a sprint"
    plan = _parse_structured_plan(text, project="Demo")
    # Fallback wraps the raw text as a single feature item; the sprint is
    # never dropped just because parsing failed.
    assert len(plan.features) == 1
    assert "totally not json" in plan.features[0].acceptance_criteria
    assert "Demo" in plan.goal


def test_structured_plan_round_trips_through_decision() -> None:
    """Decision now has structured_plan; ensure pydantic round-trips it."""
    from minions.models.decision import Decision, DecisionType

    plan = StructuredSprintPlan(
        goal="goal",
        features=[PlanItem(title="f", estimated_effort="l")],
    )
    d = Decision(
        project="Demo",
        type=DecisionType.FEATURE,
        summary="x",
        rationale="y",
        proposer_role="manager",
        proposer_agent_id="manager@Demo",
        structured_plan=plan,
        sprint_number=3,
    )
    dumped = d.model_dump(mode="json")
    assert dumped["structured_plan"]["goal"] == "goal"
    assert dumped["structured_plan"]["features"][0]["estimated_effort"] == "l"
    assert dumped["sprint_number"] == 3
    # Round-trip
    reloaded = Decision.model_validate(dumped)
    assert reloaded.structured_plan is not None
    assert reloaded.structured_plan.features[0].title == "f"
    assert reloaded.sprint_number == 3


def test_render_markdown_includes_discussion_section() -> None:
    plan = StructuredSprintPlan(
        goal="ship audit log v2",
        features=[PlanItem(title="search endpoint")],
        discussion=[
            "PO raised the mobile add-to-cart item; Principal agreed.",
            "DevOps pushed CI cleanup; Engineer said the lint failures aren't blocking — dropped.",
            "Principal proposed splitting 'Stripe totals' into 3 subtasks — accepted.",
        ],
    )
    md = plan.render_markdown()
    assert "### Discussion" in md
    assert "PO raised the mobile add-to-cart" in md
    assert "splitting 'Stripe totals' into 3 subtasks" in md


def test_planitem_subtasks_round_trip_through_decision() -> None:
    """Recursive PlanItem.subtasks survive pydantic dump/validate."""
    from minions.models.decision import Decision, DecisionType

    plan = StructuredSprintPlan(
        goal="g",
        features=[
            PlanItem(
                title="big feature",
                estimated_effort="l",
                subtasks=[
                    PlanItem(title="step 1", estimated_effort="s"),
                    PlanItem(title="step 2", estimated_effort="s"),
                ],
            ),
        ],
    )
    d = Decision(
        project="Demo",
        type=DecisionType.FEATURE,
        summary="x",
        rationale="y",
        proposer_role="manager",
        proposer_agent_id="manager@Demo",
        structured_plan=plan,
        sprint_number=3,
    )
    dumped = d.model_dump(mode="json")
    reloaded = Decision.model_validate(dumped)
    assert reloaded.structured_plan is not None
    feat = reloaded.structured_plan.features[0]
    assert len(feat.subtasks) == 2
    assert feat.subtasks[0].title == "step 1"
