"""Tenant-isolation guards for asynchronous benefit delivery."""

import uuid
from unittest.mock import AsyncMock

import pytest

from app.models.connector import Connector
from app.services.benefit_delivery_handler import _do_deliver


@pytest.mark.anyio
async def test_worker_delivery_rejects_connector_from_another_event_tenant():
    event_tenant_id = uuid.uuid4()
    connector = Connector(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        name="foreign connector",
        connector_type="generic_http",
        config={"api_url": "https://example.com"},
    )
    db = AsyncMock()

    with pytest.raises(ValueError, match="Connector does not belong to delivery tenant"):
        await _do_deliver(
            db,
            event_tenant_id,
            connector,
            "consumer-1",
            {"benefit_type": "platform_coupon"},
        )

    db.execute.assert_not_awaited()
