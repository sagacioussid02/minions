"""Structured Sprint Proposal payload.

Replaces the free-form markdown blob the planning crew used to write to
``Decision.diff_or_plan``. The Manager-stage prompt now demands strict
JSON conforming to ``StructuredSprintPlan``. ``render_markdown()`` keeps
the legacy ``diff_or_plan`` field populated so email + existing UI keep
working with zero schema awareness.

See openspec/changes/sprint-tasks-memory/ for the full design.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

EffortLevel = Literal["xs", "s", "m", "l", "xl"]


class PlanItem(BaseModel):
    """One line item in a sprint section.

    Becomes a ``Task`` after the refinement crew runs (Phase 3). For Phase 1
    these are pure data on the Decision — clickable in the UI but not yet
    assigned to a specific agent.

    For items with ``estimated_effort`` of ``l`` or ``xl`` the planning
    crew may populate ``subtasks`` (recursive). When set, the refinement
    crew creates one Task per subtask (parent title prefixed) instead of
    one Task for the parent.
    """

    model_config = ConfigDict(extra="allow")

    title: str
    rationale: str = ""
    acceptance_criteria: str = ""
    estimated_effort: EffortLevel = "m"
    # Hint, not a hard assignment. The refinement crew resolves against the
    # real roster and may pick a different agent if the suggestion is
    # unavailable or ambiguous.
    suggested_owner_role: str | None = None
    # Sub-task decomposition (Phase B of enriched-sprint-planning). Empty
    # for normal items; populated for l/xl items the planning crew chose
    # to break down. Each subtask should be ``s`` or smaller in effort.
    subtasks: list[PlanItem] = Field(default_factory=list)


class StructuredSprintPlan(BaseModel):
    """Typed sprint proposal payload."""

    model_config = ConfigDict(extra="allow")

    goal: str  # one-line theme for the sprint
    features: list[PlanItem] = Field(default_factory=list)
    bugs: list[PlanItem] = Field(default_factory=list)
    tech_debt: list[PlanItem] = Field(default_factory=list)
    ops: list[PlanItem] = Field(default_factory=list)
    docs: list[PlanItem] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    # Meeting minutes from the multi-voice debate (Phase A of
    # enriched-sprint-planning). One short line per "X pushed Y,
    # accepted/rejected because Z" turn. Empty list for the legacy
    # 3-agent path and for fallbacks.
    discussion: list[str] = Field(default_factory=list)

    def total_items(self) -> int:
        return sum(len(getattr(self, k)) for k in
                   ("features", "bugs", "tech_debt", "ops", "docs"))

    def render_markdown(self) -> str:
        """Render to the legacy ``diff_or_plan`` markdown shape.

        Sections with no items are omitted entirely. Risks render as a
        single trailing block so the email / existing UI does not have to
        treat them specially.
        """
        lines: list[str] = []
        lines.append(f"**Sprint goal:** {self.goal}".strip())
        lines.append("")
        for section_key, label in (
            ("features", "Features"),
            ("bugs", "Bugs"),
            ("tech_debt", "Tech Debt"),
            ("ops", "Ops"),
            ("docs", "Docs"),
        ):
            items: list[PlanItem] = getattr(self, section_key)
            if not items:
                continue
            lines.append(f"### {label} ({len(items)})")
            for item in items:
                owner = (
                    f" → {item.suggested_owner_role}"
                    if item.suggested_owner_role else ""
                )
                lines.append(
                    f"- **{item.title}** "
                    f"[{item.estimated_effort}]{owner}"
                )
                if item.rationale:
                    lines.append(f"  - _why_: {item.rationale}")
                if item.acceptance_criteria:
                    lines.append(f"  - _done when_: {item.acceptance_criteria}")
            lines.append("")
        if self.risks:
            lines.append("### Risks")
            for risk in self.risks:
                lines.append(f"- {risk}")
            lines.append("")
        if self.discussion:
            lines.append("### Discussion")
            for turn in self.discussion:
                lines.append(f"- {turn}")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    @classmethod
    def fallback_from_markdown(
        cls, markdown: str, goal: str = "Sprint proposal"
    ) -> StructuredSprintPlan:
        """Wrap raw markdown as a single ``features`` item.

        Used when the LLM's JSON output cannot be parsed even after a
        retry. The sprint is never dropped — the operator still sees the
        raw content, just as a single chunk.
        """
        return cls(
            goal=goal,
            features=[PlanItem(
                title="Sprint plan (unstructured)",
                rationale="Planning crew did not produce a structured plan; "
                          "raw content preserved verbatim below.",
                acceptance_criteria=markdown,
                estimated_effort="m",
            )],
        )
