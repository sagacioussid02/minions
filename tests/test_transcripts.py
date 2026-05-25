"""Minimal smoke for crew-transcripts (Phases 1-2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from minions.models.transcript import (
    MAX_MESSAGE_CHARS,
    CrewTranscriptMessage,
)
from minions.transcripts.capture import record_task
from minions.transcripts.store import TranscriptStore


def _msg(run_id: str = "run-1", sequence: int = 0, project: str = "p") -> CrewTranscriptMessage:
    return CrewTranscriptMessage(
        run_id=run_id,
        project=project,
        crew="planning",
        agent_role="product_owner",
        agent_display_name="Wren",
        sequence=sequence,
        role_in_conversation="pitch",
        content="Pitch body referencing `src/x.py:1`.",
    )


def test_store_round_trip(tmp_path: Path) -> None:
    store = TranscriptStore(tmp_path / "tr.json")
    store.save(_msg(sequence=2))
    store.save(_msg(sequence=0))
    store.save(_msg(sequence=1))
    rows = store.list_by_run("run-1")
    # Ordered by sequence regardless of insert order.
    assert [r.sequence for r in rows] == [0, 1, 2]


def test_list_for_project_caps_and_orders_recent_first(tmp_path: Path) -> None:
    store = TranscriptStore(tmp_path / "tr.json")
    for i in range(60):
        store.save(_msg(run_id=f"r-{i}", sequence=i))
    rows = store.list_for_project("p", limit=10)
    assert len(rows) == 10
    # Newest first — last-inserted run id should win.
    assert rows[0].run_id.startswith("r-")


def test_capture_persists_and_emits_activity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    activity_log = tmp_path / "activity.jsonl"
    monkeypatch.delenv("MINIONS_CREW_TRANSCRIPTS_DISABLED", raising=False)
    # Force JSONL activity sink for the test rather than Postgres.
    from minions import activity as activity_module

    activity_module.set_log_path(activity_log, force_jsonl=True)

    store = TranscriptStore(tmp_path / "tr.json")
    out = record_task(
        store=store,
        run_id="run-cap-1",
        project="Demo",
        crew="discoverer",
        agent_role="team_architect",
        agent_display_name="Beni",
        sequence=0,
        role_in_conversation="task_output",
        task_output=type("_FakeTask", (), {"raw": "Architecture markdown body."})(),
        activity_log_path=activity_log,
    )
    assert out is not None
    assert out.content == "Architecture markdown body."
    # Persisted
    assert len(store.list_by_run("run-cap-1")) == 1
    # Activity row appended
    assert activity_log.exists()
    content = activity_log.read_text()
    assert "agent_spoke" in content
    assert "Architecture markdown" in content


def test_capture_silenced_by_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINIONS_CREW_TRANSCRIPTS_DISABLED", "1")
    store = TranscriptStore(tmp_path / "tr.json")
    out = record_task(
        store=store,
        run_id="r",
        project="p",
        crew="planning",
        agent_role="product_owner",
        agent_display_name=None,
        sequence=0,
        role_in_conversation="pitch",
        task_output="anything",
    )
    assert out is None
    assert store.list_all() == []


def test_capture_truncates_to_max(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MINIONS_CREW_TRANSCRIPTS_DISABLED", raising=False)
    store = TranscriptStore(tmp_path / "tr.json")
    huge = "x" * (MAX_MESSAGE_CHARS + 5000)
    out = record_task(
        store=store,
        run_id="r",
        project="p",
        crew="planning",
        agent_role="product_owner",
        agent_display_name=None,
        sequence=0,
        role_in_conversation="pitch",
        task_output=huge,
    )
    assert out is not None
    assert len(out.content) == MAX_MESSAGE_CHARS


def test_capture_extracts_str_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MINIONS_CREW_TRANSCRIPTS_DISABLED", raising=False)
    store = TranscriptStore(tmp_path / "tr.json")
    out = record_task(
        store=store,
        run_id="r",
        project="p",
        crew="x",
        agent_role="r",
        agent_display_name=None,
        sequence=0,
        role_in_conversation="other",
        task_output="bare string output",
    )
    assert out is not None
    assert out.content == "bare string output"
