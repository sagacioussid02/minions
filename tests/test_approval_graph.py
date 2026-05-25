"""End-to-end test of the LangGraph approval graph: notify -> interrupt -> resume -> resolve."""

from langgraph.types import Command

from minions.approval.graph import build_approval_graph
from minions.approval.store import DecisionStore
from minions.models.decision import Decision, DecisionStatus, DecisionType


class _RecordingNotifier:
    def __init__(self) -> None:
        self.requests: list[Decision] = []
        self.resolutions: list[Decision] = []

    def notify_approval_request(self, decision: Decision) -> None:
        self.requests.append(decision)

    def notify_decision_resolved(self, decision: Decision) -> None:
        self.resolutions.append(decision)


def _make_decision() -> Decision:
    return Decision(
        project="Demo",
        type=DecisionType.FEATURE,
        summary="Sprint via graph",
        rationale="for unit test",
        proposer_role="manager",
        proposer_agent_id="manager@Demo",
    )


def test_graph_runs_to_interrupt_then_resumes_approve(tmp_path):
    store = DecisionStore(tmp_path / "d.json")
    notifier = _RecordingNotifier()
    decision = _make_decision()
    store.save(decision)

    graph = build_approval_graph(store, notifier)
    config = {"configurable": {"thread_id": str(decision.id)}}

    # First invoke runs notify -> wait_for_operator (then interrupts).
    result = graph.invoke({"decision_id": str(decision.id)}, config=config)
    # The graph should be interrupted; there should be no resolved status yet.
    assert "resolved_status" not in result
    assert len(notifier.requests) == 1
    assert store.get(decision.id).status is DecisionStatus.PENDING

    # Resume with operator's approval.
    final = graph.invoke(Command(resume={"action": "approve", "reason": "LGTM"}), config=config)
    assert final["resolved_status"] == "approved"
    assert store.get(decision.id).status is DecisionStatus.APPROVED
    assert store.get(decision.id).resolved_reason == "LGTM"
    assert len(notifier.resolutions) == 1


def test_graph_resumes_with_reject(tmp_path):
    store = DecisionStore(tmp_path / "d.json")
    notifier = _RecordingNotifier()
    decision = _make_decision()
    store.save(decision)

    graph = build_approval_graph(store, notifier)
    config = {"configurable": {"thread_id": str(decision.id)}}

    graph.invoke({"decision_id": str(decision.id)}, config=config)
    final = graph.invoke(
        Command(resume={"action": "reject", "reason": "out of scope"}), config=config
    )
    assert final["resolved_status"] == "rejected"
    assert store.get(decision.id).status is DecisionStatus.REJECTED
    assert store.get(decision.id).resolved_reason == "out of scope"
