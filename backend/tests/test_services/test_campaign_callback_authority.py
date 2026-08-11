import inspect
import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects.postgresql import JSONB

from app.api.v1.connectors import delivery_callback_endpoint
from app.services import campaign_callback_authority as authority


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


class _CallbackSession:
    def __init__(self, row: dict):
        self.execute = AsyncMock(return_value=_Result(row))
        self.commit = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


@pytest.mark.anyio
async def test_callback_settlement_uses_dedicated_session_and_exact_authority(monkeypatch):
    tenant_id = uuid.uuid4()
    connector_id = uuid.uuid4()
    delivery_id = uuid.uuid4()
    claim_id = uuid.uuid4()
    session = _CallbackSession(
        {
            "delivery_id": delivery_id,
            "claim_id": claim_id,
            "outbox_id": uuid.uuid4(),
            "current_status": "success",
            "replayed": False,
            "settled_at": None,
        }
    )
    bind_tenant = AsyncMock()
    monkeypatch.setattr(authority, "callback_session_factory", lambda: session)
    monkeypatch.setattr(authority, "set_session_tenant_context", bind_tenant)

    result = await authority.settle_campaign_claim_callback(
        tenant_id,
        connector_id,
        delivery_id,
        claim_id,
        "external-1",
        "success",
        {"provider": "ok"},
    )

    assert result["replayed"] is False
    bind_tenant.assert_awaited_once_with(session, tenant_id)
    statement, params = session.execute.await_args.args
    assert "settle_campaign_claim_callback" in str(statement)
    assert isinstance(statement._bindparams["external_data"].type, JSONB)
    assert params["tenant_id"] == tenant_id
    assert params["connector_id"] == connector_id
    assert params["delivery_id"] == delivery_id
    assert params["claim_id"] == claim_id
    assert params["external_id"] == "external-1"
    assert params["callback_status"] == "success"
    assert isinstance(params["audit_id"], uuid.UUID)
    session.commit.assert_awaited_once()


def test_callback_route_has_no_runtime_claim_or_delivery_mutation():
    source = inspect.getsource(delivery_callback_endpoint)

    assert "settle_campaign_claim_callback" in source
    assert ".with_for_update()" not in source
    assert "sa_update(BenefitClaim)" not in source
    assert "最新 pending" not in source
