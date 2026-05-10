import json

from typer.testing import CliRunner

import minions.__main__ as cli
from minions.models.decision import Decision, DecisionStatus, DecisionType


class FakeDecisionStore:
    def __init__(self, records: list[Decision]) -> None:
        self.records = records

    def list_all(self) -> list[Decision]:
        return self.records

    def list_by_status(self, status: DecisionStatus) -> list[Decision]:
        return [record for record in self.records if record.status is status]


def test_decisions_list_json_outputs_machine_readable_records(
    monkeypatch,
) -> None:
    decision = Decision(
        project="Demo",
        type=DecisionType.FEATURE,
        summary="Add a JSON flag",
        rationale="Scripts need stable output.",
        proposer_role="manager",
        proposer_agent_id="manager-1",
    )
    monkeypatch.setattr(cli, "_store", lambda: FakeDecisionStore([decision]))

    result = CliRunner().invoke(cli.app, ["decisions", "list", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload[0]["id"] == str(decision.id)
    assert payload[0]["summary"] == "Add a JSON flag"


def test_decisions_list_json_outputs_empty_array_for_no_records(
    monkeypatch,
) -> None:
    monkeypatch.setattr(cli, "_store", lambda: FakeDecisionStore([]))

    result = CliRunner().invoke(cli.app, ["decisions", "list", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == []
