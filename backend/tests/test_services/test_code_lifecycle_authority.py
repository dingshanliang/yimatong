import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.database import reset_request_security_credential, set_request_security_credential
from app.services.code import transition_code_batch_lifecycle, transition_code_item_lifecycle
from app.services.risk import freeze_code_item


def _controlled_result(row: dict) -> MagicMock:
    result = MagicMock()
    result.mappings.return_value.one.return_value = row
    return result


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("transition", "target_key", "row"),
    [
        (
            transition_code_item_lifecycle,
            "item_id",
            {
                "code_item_id": uuid.uuid4(),
                "prior_status": "activated",
                "current_status": "frozen",
                "recorded_at": None,
            },
        ),
        (
            transition_code_batch_lifecycle,
            "batch_id",
            {
                "code_batch_id": uuid.uuid4(),
                "affected_item_count": 3,
                "current_status": "activated",
                "recorded_at": None,
            },
        ),
    ],
)
async def test_postgresql_lifecycle_authority_receives_uuid7_audit_id(transition, target_key, row):
    tenant_id = uuid.uuid4()
    target_id = uuid.uuid4()
    session_id = uuid.uuid4()
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_controlled_result(row))
    credential_token = set_request_security_credential("auth_session", str(session_id))
    try:
        with patch("app.core.database._session_uses_postgresql", return_value=True):
            await transition(db, tenant_id, target_id, "freeze", "investigation")
    finally:
        reset_request_security_credential(credential_token)

    _, parameters = db.execute.await_args.args
    assert parameters["tenant_id"] == tenant_id
    assert parameters["auth_session_id"] == session_id
    assert parameters[target_key] == target_id
    assert parameters["audit_id"].version == 7


@pytest.mark.anyio
async def test_postgresql_manual_risk_freeze_uses_exact_authority_without_direct_fact_writes():
    tenant_id = uuid.uuid4()
    item_id = uuid.uuid4()
    session_id = uuid.uuid4()
    item = MagicMock(id=item_id, tenant_id=tenant_id, public_id="MANUAL-RISK-1", status="frozen")
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock())
    db.scalar = AsyncMock(return_value=item)
    credential_token = set_request_security_credential("auth_session", str(session_id))
    try:
        with patch("app.core.database._session_uses_postgresql", return_value=True):
            result = await freeze_code_item(
                db,
                tenant_id,
                item_id,
                actor_id=str(uuid.uuid4()),
                reason="  manual investigation  ",
                idempotency_key="manual-freeze-request-1",
            )
    finally:
        reset_request_security_credential(credential_token)

    assert result is item
    statement, parameters = db.execute.await_args.args
    assert "freeze_code_item_with_risk_alert" in str(statement)
    assert parameters["tenant_id"] == tenant_id
    assert parameters["auth_session_id"] == session_id
    assert parameters["item_id"] == item_id
    assert parameters["idempotency_key"] == "manual-freeze-request-1"
    assert parameters["reason"] == "manual investigation"
    assert parameters["receipt_id"].version == 7
    assert parameters["audit_id"].version == 7
    assert parameters["alert_id"].version == 7
    db.add.assert_not_called()
    db.flush.assert_not_awaited()
