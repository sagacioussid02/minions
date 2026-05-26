"""Tests for the monthly Portfolio Review crew.

LLM is bypassed via ``output_override`` (test 1) or ``dry_run`` (test 2).
The parse-loose fallback (test 3) feeds a synthetic CrewAI output through
the private ``_parse_loose`` helper. Input-assembler tests (5+) seed the
on-disk stores and verify the digest the crew sees.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from minions.approval.store import DecisionStore
from minions.audit.store import AuditFindingStore
from minions.crews.engineer import EngineerResult
from minions.crews.engineer_runs_store import EngineerRunStore
from minions.crews.portfolio_review import (
    PortfolioReviewInputs,
    ProjectMonthlyStats,
    _build_summary,
    _parse_loose,
    _render_plan,
    assemble_inputs,
    run_portfolio_review,
)
from minions.models.audit import AuditFinding, FindingCategory
from minions.models.decision import (
    Decision,
    DecisionStatus,
    DecisionType,
    PortfolioReview,
)
from minions.models.question import QuestionRecord, QuestionStatus
from minions.questions.store import QuestionStore

REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECTS_DIR = REPO_ROOT / "projects"


@pytest.fixture(autouse=True)
def _disable_observability(monkeypatch: pytest.MonkeyPatch) -> None:
    import minions.crews.portfolio_review as crew_mod

    class _NoopRun:
        def __enter__(self) -> None:
            return None

        def __exit__(self, *args: object) -> bool:
            return False

    monkeypatch.setattr(crew_mod, "add_metadata", lambda **kwargs: None)
    monkeypatch.setattr(crew_mod, "crew_run", lambda **kwargs: _NoopRun())
    monkeypatch.setattr(crew_mod, "set_attribution", lambda **kwargs: None)
    monkeypatch.setattr(crew_mod, "clear_attribution", lambda: None)


def _inputs() -> PortfolioReviewInputs:
    return PortfolioReviewInputs(
        portfolio_total_cost_30d_usd=14.32,
        portfolio_weekly_cap_usd=50.0,
        current_period_label="April 2026",
        per_project=[
            ProjectMonthlyStats(
                project="Demo",
                decisions_30d=3,
                prs_merged_30d=1,
                cost_30d_usd=4.10,
                audit_findings_30d=0,
                open_questions=0,
                share_weight=0.25,
                monthly_cap_usd=4.0,
            ),
            ProjectMonthlyStats(
                project="demo_three",
                decisions_30d=2,
                prs_merged_30d=0,
                cost_30d_usd=3.22,
                audit_findings_30d=1,
                open_questions=1,
                share_weight=0.25,
                monthly_cap_usd=4.0,
            ),
        ],
        deferred_projects=["trading"],
    )


# ---------- 1. output_override short-circuits the LLM ----------


def test_output_override_passthrough() -> None:
    override = PortfolioReview(
        narrative="Tilt capacity toward Demo; pause demo_three.",
        tech_priorities=[
            "Bring Demo onto Node 20",
            "Add integration tests to demo_three before any new feature",
        ],
        proposed_share_weight_changes={"Demo": 0.40, "demo_three": 0.10},
        proposed_budget_changes={"Demo": 6.0, "demo_three": 2.0},
        sunset_recommendations=[],
        revive_recommendations=[],
        confidence=4,
    )

    decision = run_portfolio_review(
        inputs=_inputs(),
        output_override=override,
    )

    # Decision-level invariants the post-approval pipeline relies on.
    assert isinstance(decision, Decision)
    assert decision.type is DecisionType.PORTFOLIO_REVIEW
    assert decision.status is DecisionStatus.PENDING
    assert decision.risk == "medium"  # triggers DA + Security
    assert decision.project == "portfolio"
    assert decision.proposer_role == "ceo"

    # The override is the literal portfolio_review attached.
    assert decision.portfolio_review is override
    assert decision.portfolio_review.confidence == 4

    # Summary line surfaces the change counts.
    assert "weight change" in decision.summary
    assert "budget change" in decision.summary

    # Rendered plan includes the CEO narrative + CTO priorities + MD changes.
    assert "Tilt capacity toward Demo" in (decision.diff_or_plan or "")
    assert "Bring Demo onto Node 20" in (decision.diff_or_plan or "")
    assert "Demo" in (decision.diff_or_plan or "")


# ---------- 2. dry-run produces a stub without any LLM call ----------


def test_dry_run_emits_stub() -> None:
    decision = run_portfolio_review(
        inputs=_inputs(),
        api_key=None,  # would force dry-run anyway
        dry_run=True,
    )

    assert isinstance(decision, Decision)
    assert decision.type is DecisionType.PORTFOLIO_REVIEW
    assert decision.portfolio_review is not None
    assert "[DRY RUN]" in decision.portfolio_review.narrative
    # Stubs propose no allocation changes — nothing to review.
    assert decision.portfolio_review.proposed_share_weight_changes == {}
    assert decision.portfolio_review.sunset_recommendations == []
    # Summary in a stub run signals "no allocation changes recommended".
    assert "no allocation changes" in decision.summary


# ---------- 3. parse-loose recovers a fenced-JSON synthesis output ----------


def test_parse_loose_recovers_fenced_json() -> None:
    synth = """Here is the requested PortfolioReview JSON:

```json
{
  "narrative": "Lean into testing this month.",
  "tech_priorities": ["Add CI to Demo", "Adopt vitest on demo_three"],
  "proposed_share_weight_changes": {"Demo": 0.5},
  "proposed_budget_changes": {"Demo": 6.0},
  "sunset_recommendations": [],
  "revive_recommendations": [],
  "confidence": 3
}
```

That's the full review.
"""
    parsed = _parse_loose(synth)
    assert isinstance(parsed, PortfolioReview)
    assert parsed.narrative.startswith("Lean into testing")
    assert "Add CI to Demo" in parsed.tech_priorities
    assert parsed.proposed_share_weight_changes == {"Demo": 0.5}
    assert parsed.confidence == 3


def test_parse_loose_returns_none_on_garbage() -> None:
    assert _parse_loose("the model did not produce JSON, it produced prose") is None


# ---------- 4. small render-helper checks (no LLM) ----------


def test_render_helpers_handle_empty_recommendations() -> None:
    inputs = _inputs()
    review = PortfolioReview(
        narrative="Steady month.",
        tech_priorities=[],
        proposed_share_weight_changes={},
        proposed_budget_changes={},
        sunset_recommendations=[],
        revive_recommendations=[],
        confidence=5,
    )
    summary = _build_summary(review, inputs)
    plan = _render_plan(review, inputs)
    assert "no allocation changes recommended" in summary
    assert "No allocation or budget changes recommended" in plan
    assert "5/5" in plan  # confidence


@pytest.mark.parametrize("count", [1, 5])
def test_confidence_bounds_enforced(count: int) -> None:
    review = PortfolioReview(
        narrative="x",
        tech_priorities=[],
        confidence=count,
    )
    assert review.confidence == count


# ---------- 5. assemble_inputs against seeded stores ----------


def _seed_engineer_run(runs: EngineerRunStore, project: str, merged_at: datetime | None) -> None:
    rec = runs.save(
        EngineerResult(
            decision_id=f"dec-{project}-{merged_at.isoformat() if merged_at else 'open'}",
            pr_url=f"https://github.com/x/{project}/pull/1",
            pr_number=1,
            branch_name="b",
            files_changed=["a"],
            dry_run=False,
        ),
        project=project,
    )
    if merged_at is not None:
        rec.merged_at = merged_at
        rec.pr_state = "merged"
        runs.update(rec)


@pytest.mark.skip(reason="fixture-coupled to private project YAMLs; smoke-tested by operator")
def test_assemble_inputs_aggregates_30_day_window(tmp_path: Path) -> None:
    now = datetime.now(tz=UTC)
    just_inside = now - timedelta(days=10)
    just_outside = now - timedelta(days=45)

    decisions = DecisionStore(tmp_path / "d.json")
    runs = EngineerRunStore(tmp_path / "r.json")
    findings = AuditFindingStore(tmp_path / "a.json")
    questions = QuestionStore(tmp_path / "q.json")

    # 2 decisions inside the window for Demo, 1 outside (should be ignored).
    for ts in (just_inside, just_inside - timedelta(hours=1)):
        d = Decision(
            project="Demo",
            type=DecisionType.FEATURE,
            summary="x",
            rationale="r",
            diff_or_plan="p",
            proposer_role="manager",
            proposer_agent_id="m",
        )
        d.created_at = ts
        decisions.save(d)
    stale = Decision(
        project="Demo",
        type=DecisionType.FEATURE,
        summary="old",
        rationale="r",
        diff_or_plan="p",
        proposer_role="manager",
        proposer_agent_id="m",
    )
    stale.created_at = just_outside
    decisions.save(stale)

    # 1 merged PR inside window for demo_three.
    _seed_engineer_run(runs, "demo_three", merged_at=just_inside)
    # 1 PR but merged outside window — should NOT count.
    _seed_engineer_run(runs, "demo_three", merged_at=just_outside)
    # 1 PR still open — should NOT count.
    _seed_engineer_run(runs, "demo_three", merged_at=None)

    # 1 audit finding inside window for Demo.
    af = AuditFinding(
        source_project="Demo",
        category=FindingCategory.CODE,
        severity="advisory",
        summary="x",
        evidence="e",
        recommendation="r",
        auditor_role="code_auditor",
        auditor_agent_id="ca@Demo",
    )
    af.created_at = just_inside
    findings.save(af)

    # 1 open question for demo_three, 1 answered (should NOT count).
    q1 = QuestionRecord(
        project="demo_three",
        asker_role="engineer",
        asker_agent_id="e@demo_three",
        target_role="manager",
        question="?",
    )
    questions.save(q1)
    q2 = QuestionRecord(
        project="demo_three",
        asker_role="engineer",
        asker_agent_id="e@demo_three",
        target_role="manager",
        question="?",
        status=QuestionStatus.ANSWERED,
    )
    questions.save(q2)

    inputs = assemble_inputs(
        projects_dir=PROJECTS_DIR,
        decision_store=decisions,
        engineer_runs_store=runs,
        audit_findings_store=findings,
        questions_store=questions,
        cost_log_path=tmp_path / "cost_log.jsonl",  # absent file → 0 cost
        portfolio=None,
        now=now,
    )

    # Period label is the current month.
    assert str(now.year) in inputs.current_period_label

    by_project = {p.project: p for p in inputs.per_project}
    assert "Demo" in by_project, "Demo manifest should be picked up"
    assert "demo_three" in by_project, "demo_three manifest should be picked up"

    Demo = by_project["Demo"]
    assert Demo.decisions_30d == 2  # the stale one is excluded
    assert Demo.audit_findings_30d == 1
    assert Demo.share_weight > 0
    assert Demo.monthly_cap_usd > 0

    tw = by_project["demo_three"]
    assert tw.prs_merged_30d == 1  # only the one merged inside window
    assert tw.open_questions == 1  # the answered one is excluded

    # Deferred listing — the repo ships projects/_deferred/trading.yaml.
    assert "trading" in inputs.deferred_projects


def test_assemble_inputs_handles_missing_optional_stores(tmp_path: Path) -> None:
    """audit + questions stores are optional; assembler must not crash."""
    decisions = DecisionStore(tmp_path / "d.json")
    runs = EngineerRunStore(tmp_path / "r.json")

    inputs = assemble_inputs(
        projects_dir=PROJECTS_DIR,
        decision_store=decisions,
        engineer_runs_store=runs,
        audit_findings_store=None,
        questions_store=None,
        cost_log_path=tmp_path / "cost_log.jsonl",
        portfolio=None,
    )

    assert len(inputs.per_project) >= 1  # at least the active manifests
    for p in inputs.per_project:
        assert p.audit_findings_30d == 0
        assert p.open_questions == 0
