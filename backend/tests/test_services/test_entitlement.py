import pytest

from app.services.entitlement import enabled_feature_flags, is_feature_enabled, validate_feature_flags


def test_feature_flags_use_one_canonical_contract_for_new_writes():
    assert validate_feature_flags(
        {
            "ai_assistant": True,
            "risk_module": False,
            "channel_portal": True,
            "white_label": False,
            "cash_red_packet": True,
        }
    ) == {
        "ai_assistant": True,
        "risk_module": False,
        "channel_portal": True,
        "white_label": False,
        "cash_red_packet": True,
    }


@pytest.mark.parametrize(
    "flags",
    [
        {"channel_store": True},
        {"unknown_feature": True},
        {"risk_module": 1},
        {"white_label": "true"},
        ["ai_assistant"],
    ],
)
def test_feature_flags_reject_legacy_unknown_and_non_boolean_writes(flags):
    with pytest.raises(ValueError):
        validate_feature_flags(flags)


def test_persisted_legacy_channel_alias_is_read_without_disabling_unrelated_features():
    flags = enabled_feature_flags(
        {
            "channel_store": True,
            "ai_assistant": True,
            "unknown_historical_flag": True,
            "risk_module": "invalid",
        }
    )

    assert flags["channel_portal"] is True
    assert flags["ai_assistant"] is True
    assert flags["risk_module"] is False
    assert is_feature_enabled(flags, "white_label") is False


def test_canonical_true_wins_over_a_false_legacy_alias_during_rollout():
    assert enabled_feature_flags({"channel_store": False, "channel_portal": True})["channel_portal"] is True


def test_non_object_persisted_features_fail_closed():
    assert not any(enabled_feature_flags(["ai_assistant"]).values())
