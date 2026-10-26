import pytest
from typer.testing import CliRunner

import minions.__main__ as cli
from minions.models.manifest import load_manifest
from minions.onboarding.scaffold import build_manifest_dict


def test_add_writes_valid_manifest(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(cli, "PROJECTS_DIR", tmp_path)
    result = CliRunner().invoke(cli.app, ["projects", "add", "acme/web-app", "--owner", "a@b.co"])
    assert result.exit_code == 0, result.output
    m = load_manifest(tmp_path / "web-app.yaml")
    assert m.source.repo == "acme/web-app"
    assert m.owner == "a@b.co"


def test_add_refuses_overwrite(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(cli, "PROJECTS_DIR", tmp_path)
    runner = CliRunner()
    assert runner.invoke(cli.app, ["projects", "add", "acme/x"]).exit_code == 0
    assert runner.invoke(cli.app, ["projects", "add", "acme/x"]).exit_code == 1


def test_bad_repo_rejected() -> None:
    with pytest.raises(ValueError):
        build_manifest_dict("not-a-repo")


def test_dashboard_hints_extra_when_streamlit_missing(monkeypatch) -> None:
    import importlib.util

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    result = CliRunner().invoke(cli.app, ["dashboard"])
    assert result.exit_code == 1
    assert "[dashboard]" in result.output
