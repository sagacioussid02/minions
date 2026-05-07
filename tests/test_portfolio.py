from pathlib import Path

from minions.config.portfolio import load_portfolio_config

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_load_portfolio_config():
    cfg = load_portfolio_config(REPO_ROOT / "config" / "portfolio.yaml")
    assert cfg.delivery_cadence.option == "A"
    assert cfg.delivery_cadence.scope == "portfolio"
    assert cfg.budget_envelope.monthly_total_floor_usd == 15
    assert cfg.budget_envelope.monthly_total_ceiling_usd == 30


def test_audit_independence():
    cfg = load_portfolio_config(REPO_ROOT / "config" / "portfolio.yaml")
    assert cfg.audit.enabled is True
    assert cfg.audit.write_access is False, "audit team must be read-only by design"
    assert cfg.audit.reports_to == "operator"


def test_procurement_safe_defaults():
    cfg = load_portfolio_config(REPO_ROOT / "config" / "portfolio.yaml")
    assert cfg.procurement.tos_acceptance_authorized is False, (
        "TOS auto-accept must remain false by default"
    )
    assert cfg.procurement.delegated_card.enabled is False, (
        "delegated paid signups must remain disabled until operator opts in"
    )
    # User-applied configuration:
    assert cfg.procurement.email_alias_template == "you+{vendor}@example.com"
    assert cfg.procurement.secret_storage == "aws_secrets_manager"
    assert cfg.procurement.delegated_card.provider == "stripe_issuing"


def test_team_composition_caps():
    cfg = load_portfolio_config(REPO_ROOT / "config" / "portfolio.yaml")
    assert cfg.team_composition.default_headcount_limits["engineers"] == 8
    assert cfg.team_composition.default_headcount_limits["total_team_size"] == 20


def test_role_definitions_present():
    cfg = load_portfolio_config(REPO_ROOT / "config" / "portfolio.yaml")
    assert "qa_engineer" in cfg.role_definitions
    assert cfg.role_definitions["qa_engineer"]["tier"] == "haiku"
