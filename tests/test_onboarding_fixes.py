"""Regression tests for first-run onboarding blockers (no GitHub token, cron exit code)."""

import pytest
from typer.testing import CliRunner

import minions.__main__ as cli
from minions.scheduled.weekly_planning import PlanningOutcome, WeeklyPlanningReport
from minions.secrets import SecretNotFound, get_anthropic_api_key


class _GithubManifest:
    name = "Demo"
    tenant_id = None

    class source:  # noqa: N801
        kind = "github"
        repo = "owner/repo"


def _no_token() -> str:
    raise SecretNotFound("no token")


def test_open_github_client_optional_degrades_without_token(monkeypatch) -> None:
    monkeypatch.setattr(cli, "get_github_token", _no_token)
    assert cli._open_github_client(_GithubManifest(), required=False) is None


def test_open_github_client_required_still_exits_without_token(monkeypatch) -> None:
    import typer

    monkeypatch.setattr(cli, "get_github_token", _no_token)
    with pytest.raises(typer.Exit):
        cli._open_github_client(_GithubManifest())


def _report(*outcomes: PlanningOutcome) -> WeeklyPlanningReport:
    return WeeklyPlanningReport(started_at="t0", finished_at="t1", outcomes=list(outcomes))


@pytest.mark.parametrize(
    ("outcomes", "code"),
    [
        ([PlanningOutcome(project="a", status="error", error="boom")], 1),
        ([PlanningOutcome(project="a", status="throttled", error="budget")], 0),
    ],
)
def test_cron_weekly_exit_code(monkeypatch, outcomes, code) -> None:
    monkeypatch.setattr("minions.scheduled.run_weekly_planning", lambda **_: _report(*outcomes))
    result = CliRunner().invoke(cli.app, ["cron", "weekly"])
    assert result.exit_code == code


def test_cron_weekly_collapses_identical_errors(monkeypatch) -> None:
    outcomes = [PlanningOutcome(project=p, status="error", error="same") for p in "abc"]
    monkeypatch.setattr("minions.scheduled.run_weekly_planning", lambda **_: _report(*outcomes))
    result = CliRunner().invoke(cli.app, ["cron", "weekly"])
    assert result.output.count("same") == 1


def test_anthropic_key_error_names_plain_env_var(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(
        "minions.secrets.get_secret", lambda name: (_ for _ in ()).throw(SecretNotFound("x"))
    )
    with pytest.raises(SecretNotFound, match="ANTHROPIC_API_KEY"):
        get_anthropic_api_key()
