from minions.approval.service import resolve, submit_for_approval
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
        summary="Sprint test",
        rationale="for unit test",
        proposer_role="manager",
        proposer_agent_id="manager@Demo",
    )


def test_submit_persists_and_notifies(tmp_path):
    store = DecisionStore(tmp_path / "d.json")
    notifier = _RecordingNotifier()
    d = _make_decision()
    submit_for_approval(d, store=store, notifier=notifier)
    assert store.get(d.id) is not None
    assert notifier.requests == [d]
    assert notifier.resolutions == []


def test_resolve_approve_updates_status_and_notifies(tmp_path):
    store = DecisionStore(tmp_path / "d.json")
    notifier = _RecordingNotifier()
    d = _make_decision()
    submit_for_approval(d, store=store, notifier=notifier)
    resolved = resolve(d.id, store=store, notifier=notifier, action="approve", reason="ok")
    assert resolved.status is DecisionStatus.APPROVED
    assert resolved.resolved_reason == "ok"
    assert len(notifier.resolutions) == 1


def test_resolve_reject_updates_status(tmp_path):
    store = DecisionStore(tmp_path / "d.json")
    notifier = _RecordingNotifier()
    d = _make_decision()
    submit_for_approval(d, store=store, notifier=notifier)
    resolved = resolve(d.id, store=store, notifier=notifier, action="reject", reason="no")
    assert resolved.status is DecisionStatus.REJECTED
    assert resolved.resolved_reason == "no"


def test_resolve_invalid_action_raises(tmp_path):
    store = DecisionStore(tmp_path / "d.json")
    notifier = _RecordingNotifier()
    d = _make_decision()
    submit_for_approval(d, store=store, notifier=notifier)
    try:
        resolve(d.id, store=store, notifier=notifier, action="merge")
    except ValueError as e:
        assert "approve" in str(e)
    else:
        raise AssertionError("expected ValueError")
