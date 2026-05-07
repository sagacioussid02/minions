from minions.models.roles import (
    TARGET_TIER,
    V0_FRUGAL_OVERRIDE,
    ModelTier,
    Role,
    resolve_tier,
)


def test_v0_drops_opus_to_sonnet():
    assert TARGET_TIER[Role.CEO] is ModelTier.OPUS
    assert V0_FRUGAL_OVERRIDE[Role.CEO] is ModelTier.SONNET


def test_engineers_stay_haiku_in_v0():
    assert V0_FRUGAL_OVERRIDE[Role.ENGINEER] is ModelTier.HAIKU
    assert V0_FRUGAL_OVERRIDE[Role.INTERN] is ModelTier.HAIKU


def test_resolve_tier_with_manifest_override():
    overrides = {Role.PRINCIPAL: ModelTier.HAIKU}
    assert resolve_tier(Role.PRINCIPAL, cadence="v0_frugal", overrides=overrides) is ModelTier.HAIKU


def test_audit_team_tiers_in_v0():
    assert resolve_tier(Role.DEVILS_ADVOCATE, cadence="v0_frugal") is ModelTier.SONNET
    assert resolve_tier(Role.PROCESS_AUDITOR, cadence="v0_frugal") is ModelTier.HAIKU
    assert resolve_tier(Role.CODE_AUDITOR, cadence="v0_frugal") is ModelTier.SONNET


def test_v2_full_returns_target():
    # v2 cadence is not yet a special-cased path in resolve_tier; non-frugal returns target.
    assert resolve_tier(Role.CEO, cadence="v2_full") is ModelTier.OPUS
    assert resolve_tier(Role.PRINCIPAL, cadence="v2_full") is ModelTier.OPUS
