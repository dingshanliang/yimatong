import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_for_consumer
from app.main import app
from tests.conftest import TestSessionLocal


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    async with TestSessionLocal() as session:
        yield session


@pytest.fixture
async def client(db_session: AsyncSession):
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db_for_consumer] = override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as api_client:
        yield api_client
    app.dependency_overrides.clear()


def _event() -> dict:
    now = datetime.now(UTC).isoformat()
    return {
        "event_id": "ORDER-API-1",
        "event_version": 3,
        "event_type": "commerce.order.refunded",
        "occurred_at": now,
        "member_ref": "cmr_api_member",
        "data": {
            "order_ref": "ORDER-API-1",
            "source_system": "medusa_v2",
            "status": "partially_refunded",
            "currency": "CNY",
            "order_original_amount_fen": 2000,
            "order_refunded_amount_fen": 1200,
            "product_original_amount_fen": 1800,
            "product_refunded_amount_fen": 1000,
            "coverage_status": "complete",
            "paid_at": now,
            "line_items": [
                {
                    "line_ref": "LINE-API-1",
                    "product_ref": "PRODUCT-API-1",
                    "quantity": 1,
                    "original_amount_fen": 1800,
                    "refunded_amount_fen": 1000,
                }
            ],
            "refunds": [
                {
                    "refund_ref": "REFUND-API-1",
                    "order_amount_fen": 1200,
                    "product_amount_fen": 1000,
                    "occurred_at": now,
                    "line_refunds": [{"line_ref": "LINE-API-1", "amount_fen": 1000}],
                }
            ],
        },
    }


@pytest.mark.anyio
async def test_signed_event_uses_credential_derived_connection_and_returns_replay_receipt(client: AsyncClient):
    credential_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    connection_id = uuid.uuid4()
    credential = SimpleNamespace(id=credential_id, tenant_id=tenant_id, connection_id=connection_id)
    connection = SimpleNamespace(id=connection_id, tenant_id=tenant_id)
    message = SimpleNamespace(message_id="ORDER-API-1", message_version=3)
    accept = AsyncMock(return_value=(message, True))

    with (
        patch(
            "app.api.v1.commerce_integrations.resolve_commerce_credential",
            new=AsyncMock(return_value=(credential, connection)),
        ),
        patch("app.api.v1.commerce_integrations.verify_commerce_signature") as verify,
        patch("app.api.v1.commerce_integrations.accept_commerce_event", new=accept),
    ):
        response = await client.post(
            "/api/v1/commerce/events",
            headers={
                "X-Commerce-Credential-Id": str(credential_id),
                "X-Commerce-Timestamp": "1787160000",
                "X-Commerce-Signature": "a" * 64,
            },
            json=_event(),
        )

    assert response.status_code == 202
    assert response.json() == {
        "message_id": "ORDER-API-1",
        "message_version": 3,
        "status": "accepted",
        "replayed": True,
    }
    verify.assert_called_once()
    assert accept.await_args.kwargs["credential"] is credential
    assert accept.await_args.kwargs["connection"] is connection
    assert accept.await_args.kwargs["event"]["event_version"] == 3


@pytest.mark.anyio
async def test_invalid_signature_stops_before_any_inbox_side_effect(client: AsyncClient):
    credential_id = uuid.uuid4()
    credential = SimpleNamespace(id=credential_id, tenant_id=uuid.uuid4(), connection_id=uuid.uuid4())
    connection = SimpleNamespace(id=credential.connection_id, tenant_id=credential.tenant_id)
    accept = AsyncMock()

    with (
        patch(
            "app.api.v1.commerce_integrations.resolve_commerce_credential",
            new=AsyncMock(return_value=(credential, connection)),
        ),
        patch(
            "app.api.v1.commerce_integrations.verify_commerce_signature",
            side_effect=HTTPException(status_code=401, detail="invalid_commerce_signature"),
        ),
        patch("app.api.v1.commerce_integrations.accept_commerce_event", new=accept),
    ):
        response = await client.post(
            "/api/v1/commerce/events",
            headers={
                "X-Commerce-Credential-Id": str(credential_id),
                "X-Commerce-Timestamp": "1787160000",
                "X-Commerce-Signature": "0" * 64,
            },
            json=_event(),
        )

    assert response.status_code == 401
    assert response.json()["detail"] == "invalid_commerce_signature"
    accept.assert_not_awaited()
