"""Interview-relay unit tests.

The DB-touching paths are stubbed via monkeypatching so these run offline.
Focus is on the decision-path: which payloads relay, which are silently
no-op, and the answer-text composition for the chat thread.
"""

from __future__ import annotations

from typing import Any

import pytest

from minions.crews.engineer import EngineerResult
from minions.spokesperson import interview_relay


def _result(**overrides: Any) -> EngineerResult:
    base: dict[str, Any] = {
        "decision_id": "abc-123",
        "pr_url": "https://github.com/x/demo_four/pull/2",
        "pr_number": 2,
        "branch_name": "minions/eng/spike",
        "files_changed": ["vercel.json", "package.json"],
        "files_rejected": [],
        "operator_comment_posted": True,
        "skipped": False,
        "skip_reason": None,
        "dry_run": False,
    }
    base.update(overrides)
    return EngineerResult(**base)


def test_relay_no_op_when_not_spike(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(interview_relay, "has_database_url", lambda: True)
    monkeypatch.setattr(
        interview_relay, "_load_decision_payload", lambda decision_id: {"spike_source": None}
    )
    assert (
        interview_relay.relay_spike_answer(
            decision_id="abc",
            project="demo_four",
            engineer_result=_result(),
        )
        is None
    )


def test_relay_no_op_when_missing_thread_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(interview_relay, "has_database_url", lambda: True)
    monkeypatch.setattr(
        interview_relay,
        "_load_decision_payload",
        lambda decision_id: {"spike_source": "spokesperson_interview", "question": "Q"},
    )
    assert (
        interview_relay.relay_spike_answer(
            decision_id="abc",
            project="demo_four",
            engineer_result=_result(),
        )
        is None
    )


def test_relay_no_op_when_no_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(interview_relay, "has_database_url", lambda: False)
    assert (
        interview_relay.relay_spike_answer(
            decision_id="abc",
            project="demo_four",
            engineer_result=_result(),
        )
        is None
    )


def test_relay_inserts_when_thread_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(interview_relay, "has_database_url", lambda: True)
    monkeypatch.setattr(
        interview_relay,
        "_load_decision_payload",
        lambda decision_id: {
            "spike_source": "spokesperson_interview",
            "thread_id": "11111111-1111-1111-1111-111111111111",
            "question": "Where is demo_four deployed?",
            "proposer_role": "cloud_devops",
            "requested_by_role": "cto",
            "consulted_roles": ["cto", "cloud_devops"],
        },
    )
    captured: dict[str, Any] = {}

    def fake_insert(
        *, message_id: str, thread_id: str, agent_role: str, payload: dict[str, Any]
    ) -> None:
        captured["message_id"] = message_id
        captured["thread_id"] = thread_id
        captured["agent_role"] = agent_role
        captured["payload"] = payload

    monkeypatch.setattr(interview_relay, "_insert_message_and_touch_thread", fake_insert)

    msg_id = interview_relay.relay_spike_answer(
        decision_id="abc",
        project="demo_four",
        engineer_result=_result(),
    )

    assert msg_id is not None
    assert captured["thread_id"] == "11111111-1111-1111-1111-111111111111"
    assert captured["agent_role"] == "cloud_devops"
    assert "Where is demo_four deployed?" in captured["payload"]["content"]
    assert "pull/2" in captured["payload"]["content"]
    # citations include the PR + each inspected file
    labels = [c["label"] for c in captured["payload"]["citations"]]
    assert "PR #2" in labels
    assert "vercel.json" in labels
    assert captured["payload"]["spike_decision_id"] == "abc"


def test_compose_answer_handles_skipped_run() -> None:
    text = interview_relay._compose_answer(
        question="Q?",
        owner_role="cloud_devops",
        project="demo_four",
        result=_result(skipped=True, skip_reason="forbidden paths"),
    )
    assert "skipped" in text.lower()
    assert "forbidden paths" in text


def test_compose_answer_handles_no_pr() -> None:
    text = interview_relay._compose_answer(
        question="Q?",
        owner_role="cloud_devops",
        project="demo_four",
        result=_result(pr_url=None, pr_number=None),
    )
    assert "did not open a PR" in text


def test_compose_answer_truncates_long_file_lists() -> None:
    text = interview_relay._compose_answer(
        question="Q?",
        owner_role="cloud_devops",
        project="demo_four",
        result=_result(files_changed=[f"f{i}.ts" for i in range(20)]),
    )
    assert "+14 more" in text  # 20 - 6 shown
