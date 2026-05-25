"""Auto-priority classifier on Decision construction.

The ``requested_by_role`` field is the *signal* that a Decision was filed on
behalf of a leadership role. The model validator turns that signal into a
default priority/expedited stamp so the operator does not have to do it
manually. Explicit priority/expedited from the caller always wins.
"""

from minions.models.decision import (
    Decision,
    DecisionType,
    default_priority_for_role,
)


def _base_kwargs(**extra: object) -> dict[str, object]:
    base: dict[str, object] = {
        "project": "Demo",
        "type": DecisionType.FEATURE,
        "summary": "test",
        "rationale": "test",
        "risk": "low",
        "proposer_role": "manager",
        "proposer_agent_id": "manager@Demo",
    }
    base.update(extra)
    return base


def test_cto_request_auto_promotes_to_p1_expedited() -> None:
    d = Decision(**_base_kwargs(requested_by_role="cto"))
    assert d.priority == "p1"
    assert d.expedited is True


def test_ceo_and_md_also_p1() -> None:
    for role in ("ceo", "md", "managing_director", "chair", "board", "chief_product_officer", "coo"):
        d = Decision(**_base_kwargs(requested_by_role=role))
        assert d.priority == "p1", role
        assert d.expedited is True, role


def test_principal_and_pm_are_p2_expedited() -> None:
    for role in (
        "principal", "principal_engineer", "pm", "product_manager",
        "portfolio_owner", "security_champion", "spokesperson",
    ):
        d = Decision(**_base_kwargs(requested_by_role=role))
        assert d.priority == "p2", role
        assert d.expedited is True, role


def test_unknown_role_keeps_defaults() -> None:
    d = Decision(**_base_kwargs(requested_by_role="random_intern"))
    assert d.priority == "p3"
    assert d.expedited is False


def test_no_requested_by_role_keeps_defaults() -> None:
    d = Decision(**_base_kwargs())
    assert d.priority == "p3"
    assert d.expedited is False


def test_explicit_priority_overrides_role_default() -> None:
    # Operator explicitly downgrades a CTO-flagged Decision — respect that.
    d = Decision(**_base_kwargs(requested_by_role="cto", priority="p2"))
    assert d.priority == "p2"
    # expedited remained at default False -> validator sees priority != "p3"
    # and skips the role lookup entirely.
    assert d.expedited is False


def test_explicit_expedited_overrides_role_default() -> None:
    d = Decision(**_base_kwargs(requested_by_role="cto", expedited=True))
    # priority default "p3" with expedited explicitly True — validator skips
    # because expedited was set explicitly.
    assert d.priority == "p3"
    assert d.expedited is True


def test_role_lookup_is_case_insensitive() -> None:
    d = Decision(**_base_kwargs(requested_by_role="CTO"))
    assert d.priority == "p1"
    assert d.expedited is True


def test_default_priority_for_role_helper() -> None:
    assert default_priority_for_role("cto") == ("p1", True)
    assert default_priority_for_role("pm") == ("p2", True)
    assert default_priority_for_role("unknown") is None
    assert default_priority_for_role(None) is None
