import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects.postgresql import JSONB

from app.core.database import reset_request_security_credential, set_request_security_credential
from app.services.campaign_authority import (
    claim_campaign_benefit_authority,
    create_benefit_authority,
    create_campaign_authority,
    redeem_campaign_benefit_claim_authority,
    transition_campaign_authority,
    update_benefit_authority,
    update_campaign_authority,
)


class _Mappings:
    def __init__(self, row: dict):
        self._row = row

    def one(self):
        return self._row


class _Result:
    def __init__(self, row: dict):
        self._row = row

    def mappings(self):
        return _Mappings(self._row)


@pytest.mark.anyio
async def test_manual_transition_requires_live_auth_session():
    db = AsyncMock()
    with pytest.raises(Exception) as caught:
        await transition_campaign_authority(db, uuid.uuid4(), uuid.uuid4(), "active")

    assert getattr(caught.value, "status_code", None) == 401
    db.execute.assert_not_awaited()


@pytest.mark.anyio
async def test_claim_authority_passes_exact_scan_and_product_facts():
    db = AsyncMock()
    db.execute.return_value = _Result(
        {
            "outcome": "success",
            "claim_id": uuid.uuid4(),
            "campaign_id": uuid.uuid4(),
            "stock_used": 1,
            "outbox_id": uuid.uuid4(),
            "created": True,
        }
    )
    tenant_id = uuid.uuid4()
    benefit_id = uuid.uuid4()
    scan_event_id = uuid.uuid4()
    product_id = uuid.uuid4()

    result = await claim_campaign_benefit_authority(
        db,
        tenant_id,
        benefit_id,
        scan_event_id,
        product_id,
        "PUBLIC001",
        "consumer-1",
        "claim:v1:" + "a" * 64,
    )

    assert result["created"] is True
    params = db.execute.await_args.args[1]
    assert params["tenant_id"] == tenant_id
    assert params["benefit_id"] == benefit_id
    assert params["scan_event_id"] == scan_event_id
    assert params["scanned_product_id"] == product_id
    assert params["public_id"] == "PUBLIC001"
    assert params["idempotency_key"] == "claim:v1:" + "a" * 64


@pytest.mark.anyio
async def test_redeem_authority_binds_api_key_and_generates_audit_identity():
    db = AsyncMock()
    claim_id = uuid.uuid4()
    db.execute.return_value = _Result({"claim_id": claim_id, "current_status": "used", "updated_at": None})
    tenant_id = uuid.uuid4()
    api_key_id = uuid.uuid4()

    result = await redeem_campaign_benefit_claim_authority(db, tenant_id, api_key_id, claim_id)

    assert result["current_status"] == "used"
    statement, params = db.execute.await_args.args
    assert "redeem_campaign_benefit_claim" in str(statement)
    assert params["tenant_id"] == tenant_id
    assert params["api_key_id"] == api_key_id
    assert params["claim_id"] == claim_id
    assert isinstance(params["audit_id"], uuid.UUID)


@pytest.mark.anyio
async def test_manual_transition_derives_session_and_audit_identity():
    db = AsyncMock()
    db.execute.return_value = _Result(
        {
            "campaign_id": uuid.uuid4(),
            "current_status": "active",
            "published_at": None,
            "updated_at": None,
        }
    )
    session_id = uuid.uuid4()
    token = set_request_security_credential("auth_session", str(session_id))
    try:
        await transition_campaign_authority(db, uuid.uuid4(), uuid.uuid4(), "active")
    finally:
        reset_request_security_credential(token)

    params = db.execute.await_args.args[1]
    assert params["auth_session_id"] == session_id
    assert isinstance(params["audit_id"], uuid.UUID)
    assert params["target_status"] == "active"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("call", "parameter"),
    [
        (
            lambda db, tenant: create_campaign_authority(
                db,
                tenant,
                uuid.uuid4(),
                uuid.uuid4(),
                "活动",
                "promotion",
                None,
                None,
                {"participation_condition_type": "any_scan"},
                None,
            ),
            "rules_json",
        ),
        (
            lambda db, tenant: create_benefit_authority(
                db,
                tenant,
                uuid.uuid4(),
                uuid.uuid4(),
                "权益",
                "platform_coupon",
                {"validity_type": "fixed_range"},
                10,
                1,
                None,
            ),
            "config_json",
        ),
        (
            lambda db, tenant: update_campaign_authority(
                db,
                tenant,
                uuid.uuid4(),
                {"rules_json": {"participation_condition_type": "member_only"}},
            ),
            "rules_json",
        ),
        (
            lambda db, tenant: update_benefit_authority(
                db,
                tenant,
                uuid.uuid4(),
                {"config_json": {"validity_type": "after_claim_days"}},
            ),
            "config_json",
        ),
    ],
)
async def test_json_authority_parameters_have_explicit_postgresql_types(call, parameter):
    db = AsyncMock()
    db.execute.return_value = _Result(
        {
            "campaign_id": uuid.uuid4(),
            "benefit_id": uuid.uuid4(),
            "current_status": "draft",
            "updated_at": None,
        }
    )
    session_id = uuid.uuid4()
    token = set_request_security_credential("auth_session", str(session_id))
    try:
        await call(db, uuid.uuid4())
    finally:
        reset_request_security_credential(token)

    statement = db.execute.await_args.args[0]
    assert isinstance(statement._bindparams[parameter].type, JSONB)
