"""Metadata contract for tenant-bound consumer consent authority."""

import importlib.util
from pathlib import Path

import pytest

from app.models.consent import (
    ConsentRecord,
    ConsumerConsentAction,
    ConsumerConsentPolicy,
    ConsumerConsentPolicyCurrent,
)
from app.models.member import ConsumerPhoneEncryptionKey, ConsumerProfile


def _foreign_key_columns(table) -> set[tuple[tuple[str, ...], tuple[str, ...]]]:
    return {
        (
            tuple(element.parent.name for element in constraint.elements),
            tuple(element.target_fullname for element in constraint.elements),
        )
        for constraint in table.foreign_key_constraints
    }


def test_consumer_consent_metadata_is_tenant_bound() -> None:
    assert (
        ("tenant_id", "consumer_id"),
        ("consumer_profiles.tenant_id", "consumer_profiles.id"),
    ) in _foreign_key_columns(ConsentRecord.__table__)
    assert (
        ("tenant_id", "policy_id"),
        ("consumer_consent_policies.tenant_id", "consumer_consent_policies.id"),
    ) in _foreign_key_columns(ConsentRecord.__table__)
    assert (("tenant_id", "consent_id"), ("consent_records.tenant_id", "consent_records.id")) in _foreign_key_columns(
        ConsumerConsentAction.__table__
    )
    assert (
        ("tenant_id", "lead_consent_id"),
        ("consent_records.tenant_id", "consent_records.id"),
    ) in _foreign_key_columns(ConsumerProfile.__table__)
    assert {constraint.name for constraint in ConsumerConsentPolicy.__table__.constraints} >= {
        "uq_consumer_consent_policies_tenant_id",
        "uq_consumer_consent_policies_tenant_purpose_id",
        "uq_consumer_consent_policies_tenant_purpose_version",
    }
    assert (
        ("tenant_id", "purpose", "policy_id"),
        (
            "consumer_consent_policies.tenant_id",
            "consumer_consent_policies.purpose",
            "consumer_consent_policies.id",
        ),
    ) in _foreign_key_columns(ConsumerConsentPolicyCurrent.__table__)
    assert "phone_encrypted" not in ConsumerProfile.__table__.c
    assert {"phone_ciphertext", "phone_nonce", "phone_key_id"} <= set(ConsumerProfile.__table__.c.keys())
    assert ConsumerPhoneEncryptionKey.__table__.c.key_id.primary_key


def test_legacy_0007_downgrade_preflights_before_drop(monkeypatch: pytest.MonkeyPatch) -> None:
    path = Path(__file__).parents[2] / "alembic" / "versions" / "0007_export_log_and_consents.py"
    spec = importlib.util.spec_from_file_location("legacy_consent_0007", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class _Result:
        @staticmethod
        def scalar_one() -> bool:
            return True

    class _Bind:
        dialect = type("Dialect", (), {"name": "postgresql"})()

        @staticmethod
        def execute(_statement):
            return _Result()

    monkeypatch.setattr(module.op, "get_bind", lambda: _Bind())
    monkeypatch.setattr(module.op, "drop_table", lambda _table: pytest.fail("drop ran before populated preflight"))
    with pytest.raises(RuntimeError, match="consent or export audit facts exist"):
        module.downgrade()
