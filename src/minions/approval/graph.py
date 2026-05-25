"""LangGraph approval state machine — the canonical orchestration pattern.

Flow: ``notify -> wait_for_operator (interrupt) -> resolve``.

For v0 the checkpointer is in-memory (single process, single thread). Production
swaps for ``langgraph.checkpoint.sqlite.SqliteSaver`` or
``langgraph.checkpoint.postgres.PostgresSaver`` so cross-process resume works.
The CLI's cross-process ``decisions approve/reject`` commands take a simpler
direct-to-store path (see :mod:`minions.approval.service`); this graph is the
durable in-process pattern that crews invoke when proposing work.
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, StateGraph
from langgraph.types import interrupt

from minions.approval.store import DecisionStore
from minions.models.decision import DecisionStatus
from minions.notify.base import Notifier


class ApprovalState(TypedDict, total=False):
    decision_id: str
    operator_response: dict[str, Any]
    resolved_status: str


def build_approval_graph(store: DecisionStore, notifier: Notifier):
    """Build and compile the approval graph.

    Returns a compiled graph. To use:

        graph = build_approval_graph(store, notifier)
        config = {"configurable": {"thread_id": str(decision.id)}}
        graph.invoke({"decision_id": str(decision.id)}, config=config)
        # ... operator approves / rejects ...
        graph.invoke(Command(resume={"action": "approve"}), config=config)
    """

    def notify_node(state: ApprovalState) -> dict[str, Any]:
        decision = store.get(state["decision_id"])
        if decision is None:
            raise KeyError(state["decision_id"])
        notifier.notify_approval_request(decision)
        return {}

    def wait_for_operator_node(state: ApprovalState) -> dict[str, Any]:
        # Halts until the graph is resumed with a Command(resume=...).
        response = interrupt({"decision_id": state["decision_id"]})
        return {"operator_response": response}

    def resolve_node(state: ApprovalState) -> dict[str, Any]:
        response = state.get("operator_response") or {}
        action = response.get("action", "reject")
        reason = response.get("reason")
        new_status = (
            DecisionStatus.APPROVED if action == "approve" else DecisionStatus.REJECTED
        )
        store.update_status(state["decision_id"], new_status, reason=reason)
        decision = store.get(state["decision_id"])
        if decision is not None:
            notifier.notify_decision_resolved(decision)
        return {"resolved_status": new_status.value}

    builder: StateGraph = StateGraph(ApprovalState)
    builder.add_node("notify", notify_node)
    builder.add_node("wait_for_operator", wait_for_operator_node)
    builder.add_node("resolve", resolve_node)
    builder.set_entry_point("notify")
    builder.add_edge("notify", "wait_for_operator")
    builder.add_edge("wait_for_operator", "resolve")
    builder.add_edge("resolve", END)

    return builder.compile(checkpointer=InMemorySaver())
