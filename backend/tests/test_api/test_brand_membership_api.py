import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db, get_db_for_consumer, get_db_with_bypass
from app.main import app
from app.services.scan_token import create_scan_token
from app.services.wechat_miniprogram import VerifiedMiniProgramIdentity
from app.utils.client_ip import compute_ip_hash
from tests.conftest import TestSessionLocal


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    async with TestSessionLocal() as session:
        yield session


@pytest.fixture
async def client(db_session: AsyncSession):
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_db_with_bypass] = override_get_db
    app.dependency_overrides[get_db_for_consumer] = override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as api_client:
        yield api_client
    app.dependency_overrides.clear()


def _token(*, consumer_id: uuid.UUID | None = None, ip: str = "203.0.113.18") -> str:
    return create_scan_token(
        public_id="MEMBER-CODE",
        ip_hash=compute_ip_hash(ip),
        tenant_id=str(uuid.uuid4()),
        consumer_id=str(consumer_id) if consumer_id else "",
        scan_event_id=str(uuid.uuid4()),
        scan_time=datetime.now(UTC).isoformat(),
        visitor_id=str(uuid.uuid4()),
    )


@pytest.mark.anyio
async def test_join_membership_binds_returned_consumer_to_scan_credential(client: AsyncClient):
    consumer_id = uuid.uuid4()
    membership_id = uuid.uuid4()
    join = AsyncMock(
        return_value={
            "membership_id": membership_id,
            "membership_number": "MBR-TEST00000001",
            "consumer_id": consumer_id,
            "status": "active",
            "joined_at": datetime.now(UTC),
            "replayed": False,
        }
    )
    with (
        patch("app.api.v1.consumers.get_client_ip", return_value="203.0.113.18"),
        patch("app.api.v1.consumers.enforce_public_consumer_admission", new=AsyncMock()),
        patch("app.api.v1.consumers._require_consumer_business_plan", new=AsyncMock(return_value=None)),
        patch("app.api.v1.consumers.join_brand_membership", new=join),
        patch("app.api.v1.consumers.link_visitor_to_consumer", new=AsyncMock(return_value=True)),
    ):
        response = await client.post(
            "/api/v1/consumers/membership/join",
            headers={"Authorization": f"Bearer {_token()}"},
            json={"consent_id": str(uuid.uuid4()), "idempotency_key": "membership-join-api-1"},
        )

    assert response.status_code == 201
    assert response.json()["membership_id"] == str(membership_id)
    assert response.json()["consumer_id"] == str(consumer_id)
    assert response.json()["scan_token"]
    join.assert_awaited_once()


@pytest.mark.anyio
async def test_recovery_requires_consumer_bound_scan_credential(client: AsyncClient):
    with patch("app.api.v1.consumers.get_client_ip", return_value="203.0.113.18"):
        response = await client.post(
            "/api/v1/consumers/membership/recover",
            headers={"Authorization": f"Bearer {_token()}"},
            json={"recovery_token": "r" * 32, "idempotency_key": "membership-recover-api-1"},
        )

    assert response.status_code == 401
    assert response.json()["detail"] == "consumer-bound scan_token required"


@pytest.mark.anyio
async def test_merge_routes_two_proofs_from_consumer_bound_credential(client: AsyncClient):
    consumer_id = uuid.uuid4()
    merge = AsyncMock(
        return_value={
            "membership_id": uuid.uuid4(),
            "membership_number": "MBR-MERGED000001",
            "consumer_id": consumer_id,
            "status": "active",
            "joined_at": datetime.now(UTC),
            "replayed": False,
        }
    )
    with (
        patch("app.api.v1.consumers.get_client_ip", return_value="203.0.113.18"),
        patch("app.api.v1.consumers.merge_brand_memberships", new=merge),
    ):
        response = await client.post(
            "/api/v1/consumers/membership/merge",
            headers={"Authorization": f"Bearer {_token(consumer_id=consumer_id)}"},
            json={
                "current_recovery_token": "c" * 32,
                "target_recovery_token": "t" * 32,
                "idempotency_key": "membership-merge-api-1",
            },
        )

    assert response.status_code == 200
    merge.assert_awaited_once()
    assert merge.await_args.kwargs["current_consumer_id"] == consumer_id


@pytest.mark.anyio
async def test_miniprogram_session_does_not_expose_unbound_wechat_subject(client: AsyncClient):
    with (
        patch("app.api.v1.consumers.get_client_ip", return_value="203.0.113.18"),
        patch("app.api.v1.consumers.enforce_public_consumer_admission", new=AsyncMock()),
        patch("app.api.v1.consumers._require_consumer_business_plan", new=AsyncMock(return_value=None)),
        patch(
            "app.api.v1.consumers.exchange_miniprogram_code",
            new=AsyncMock(return_value=VerifiedMiniProgramIdentity("shared-appid", "private-openid")),
        ),
        patch("app.api.v1.consumers.get_membership_by_verified_identity", new=AsyncMock(return_value=None)),
    ):
        response = await client.post(
            "/api/v1/consumers/membership/miniprogram-session",
            headers={"Authorization": f"Bearer {_token()}"},
            json={"js_code": "one-time-code"},
        )

    assert response.status_code == 200
    assert response.json() == {"status": "unbound"}
    assert "private-openid" not in response.text


@pytest.mark.anyio
async def test_miniprogram_session_restores_existing_member_and_rebinds_scan_token(
    client: AsyncClient, db_session: AsyncSession
):
    consumer_id = uuid.uuid4()
    membership_id = uuid.uuid4()
    credential_id = uuid.uuid4()
    membership = SimpleNamespace(id=membership_id)
    credential = SimpleNamespace(id=credential_id)
    existing = {
        "membership_id": membership_id,
        "membership_number": "MBR-RESTORED001",
        "consumer_id": consumer_id,
        "status": "active",
        "joined_at": datetime.now(UTC),
        "replayed": False,
    }
    with (
        patch.object(db_session, "scalar", new=AsyncMock(return_value=SimpleNamespace(id=consumer_id))),
        patch("app.api.v1.consumers.get_client_ip", return_value="203.0.113.18"),
        patch("app.api.v1.consumers.enforce_public_consumer_admission", new=AsyncMock()),
        patch("app.api.v1.consumers._require_consumer_business_plan", new=AsyncMock(return_value=None)),
        patch(
            "app.api.v1.consumers.exchange_miniprogram_code",
            new=AsyncMock(return_value=VerifiedMiniProgramIdentity("shared-appid", "private-openid")),
        ),
        patch(
            "app.api.v1.consumers.get_membership_by_verified_identity",
            new=AsyncMock(return_value=(membership, credential)),
        ),
        patch("app.api.v1.consumers.get_brand_membership_for_profile", new=AsyncMock(return_value=existing)),
        patch("app.api.v1.consumers.link_visitor_to_consumer", new=AsyncMock(return_value=True)),
    ):
        response = await client.post(
            "/api/v1/consumers/membership/miniprogram-session",
            headers={"Authorization": f"Bearer {_token(consumer_id=consumer_id)}"},
            json={"js_code": "one-time-code"},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "recovered"
    assert response.json()["membership"]["membership_id"] == str(membership_id)
    assert response.json()["scan_token"]
    assert "private-openid" not in response.text


@pytest.mark.anyio
async def test_miniprogram_bind_requires_existing_member_and_returns_short_recovery_token(client: AsyncClient):
    consumer_id = uuid.uuid4()
    membership_id = uuid.uuid4()
    credential_id = uuid.uuid4()
    membership = {
        "membership_id": membership_id,
        "membership_number": "MBR-BIND0000001",
        "consumer_id": consumer_id,
        "status": "active",
        "joined_at": datetime.now(UTC),
        "replayed": False,
    }
    credential = SimpleNamespace(id=credential_id, membership_id=membership_id)
    with (
        patch("app.api.v1.consumers.get_client_ip", return_value="203.0.113.18"),
        patch("app.api.v1.consumers.enforce_public_consumer_admission", new=AsyncMock()),
        patch("app.api.v1.consumers.get_brand_membership_for_profile", new=AsyncMock(return_value=membership)),
        patch(
            "app.api.v1.consumers.exchange_miniprogram_code",
            new=AsyncMock(return_value=VerifiedMiniProgramIdentity("shared-appid", "private-openid")),
        ),
        patch("app.api.v1.consumers.bind_verified_member_identity", new=AsyncMock(return_value=credential)),
    ):
        response = await client.post(
            "/api/v1/consumers/membership/miniprogram-bind",
            headers={"Authorization": f"Bearer {_token(consumer_id=consumer_id)}"},
            json={"js_code": "one-time-code", "idempotency_key": "miniprogram-bind-api-1"},
        )

    assert response.status_code == 201
    assert response.json()["status"] == "bound"
    assert response.json()["expires_in"] == 300
    assert response.json()["recovery_token"]
    assert "private-openid" not in response.text
