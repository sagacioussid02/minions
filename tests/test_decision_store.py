import pytest

from minions.approval.store import DecisionStore
from minions.models.decision import Decision, DecisionStatus, DecisionType


def _decision(project: str = "Demo", risk: str = "low") -> Decision:
    return Decision(
        project=project,
        type=DecisionType.FEATURE,
        summary="Test sprint",
        rationale="for unit test",
        risk=risk,
        proposer_role="manager",
        proposer_agent_id=f"manager@{project}",
    )


def test_save_and_get(tmp_path):
    store = DecisionStore(tmp_path / "decisions.json")
    d = _decision()
    store.save(d)
    fetched = store.get(d.id)
    assert fetched is not None
    assert fetched.id == d.id
    assert fetched.project == "Demo"


def test_get_missing_returns_none(tmp_path):
    store = DecisionStore(tmp_path / "decisions.json")
    assert store.get("00000000-0000-0000-0000-000000000000") is None


def test_list_by_status(tmp_path):
    store = DecisionStore(tmp_path / "decisions.json")
    a = _decision("Demo")
    b = _decision("demo_three")
    store.save(a)
    store.save(b)
    pending = store.list_by_status(DecisionStatus.PENDING)
    assert {d.id for d in pending} == {a.id, b.id}
    assert store.list_by_status(DecisionStatus.APPROVED) == []


def test_update_status_records_resolution(tmp_path):
    store = DecisionStore(tmp_path / "decisions.json")
    d = _decision()
    store.save(d)
    updated = store.update_status(d.id, DecisionStatus.APPROVED, reason="LGTM")
    assert updated.status == DecisionStatus.APPROVED
    assert updated.resolved_at is not None
    assert updated.resolved_reason == "LGTM"
    # Persisted across re-load:
    assert store.get(d.id).status == DecisionStatus.APPROVED


def test_update_status_missing_raises(tmp_path):
    store = DecisionStore(tmp_path / "decisions.json")
    with pytest.raises(KeyError):
        store.update_status("00000000-0000-0000-0000-000000000000", DecisionStatus.APPROVED)
