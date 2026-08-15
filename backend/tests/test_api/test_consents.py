import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db, get_db_for_consumer, get_db_with_bypass
from app.main import app
from app.services.scan_token import create_scan_token
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


def _token(ip: str = "203.0.113.9", consumer_id: uuid.UUID | None = None) -> str:
    return create_scan_token(
        public_id="CONSENT-CODE",
        ip_hash=compute_ip_hash(ip),
        tenant_id=str(uuid.uuid4()),
        scan_event_id=str(uuid.uuid4()),
        scan_time=datetime.now(UTC).isoformat(),
        visitor_id=str(uuid.uuid4()),
        consumer_id=str(consumer_id) if consumer_id else "",
    )


@pytest.mark.anyio
async def test_consent_grant_returns_only_after_authoritative_receipt(client: AsyncClient):
    consent_id = uuid.uuid4()
    authority = AsyncMock(
        return_value={
            "consent_id": consent_id,
            "status": "granted",
            "purpose": "lead_capture",
            "policy_version": "v2",
            "policy_digest": "a" * 64,
            "consumer_id": None,
            "granted_at": datetime.now(UTC),
            "replayed": False,
        }
    )
    with (
        patch("app.api.v1.consents.get_client_ip", return_value="203.0.113.9"),
        patch("app.api.v1.consents.enforce_public_consumer_admission", new=AsyncMock()),
        patch("app.api.v1.consents.grant_consumer_consent_authority", new=authority),
    ):
        response = await client.post(
            "/api/v1/public/consents",
            headers={"Authorization": f"Bearer {_token()}"},
            json={
                "purpose": "lead_capture",
                "policy_version": "v2",
                "policy_digest": "a" * 64,
                "idempotency_key": f"grant-{uuid.uuid4()}",
            },
        )

    assert response.status_code == 201
    assert response.json()["consent_id"] == str(consent_id)
    authority.assert_awaited_once()


@pytest.mark.anyio
async def test_consent_withdraw_subject_denial_is_not_reported_as_success(client: AsyncClient):
    with (
        patch("app.api.v1.consents.get_client_ip", return_value="203.0.113.9"),
        patch("app.api.v1.consents.enforce_public_consumer_admission", new=AsyncMock()),
        patch(
            "app.api.v1.consents.withdraw_consumer_consent_authority",
            new=AsyncMock(side_effect=HTTPException(status_code=403, detail="consent_authority_denied")),
        ),
    ):
        response = await client.post(
            f"/api/v1/public/consents/{uuid.uuid4()}/withdraw",
            headers={"Authorization": f"Bearer {_token()}"},
            json={"idempotency_key": f"withdraw-{uuid.uuid4()}"},
        )

    assert response.status_code == 403
    assert response.json()["detail"] == "consent_authority_denied"


@pytest.mark.anyio
async def test_consent_receipt_status_uses_exact_scan_authority(client: AsyncClient):
    consent_id = uuid.uuid4()
    authority = AsyncMock(
        return_value={
            "consent_id": consent_id,
            "status": "granted",
            "purpose": "privacy_policy",
            "policy_version": "v2",
            "policy_digest": "a" * 64,
            "consumer_id": None,
            "granted_at": datetime.now(UTC),
            "withdrawn_at": None,
        }
    )
    with (
        patch("app.api.v1.consents.get_client_ip", return_value="203.0.113.9"),
        patch(
            "app.api.v1.consents.get_consumer_consent_receipt_status",
            new=authority,
        ),
    ):
        response = await client.get(
            f"/api/v1/public/consents/{consent_id}/status",
            headers={"Authorization": f"Bearer {_token()}"},
        )

    assert response.status_code == 200
    assert response.json()["consent_id"] == str(consent_id)
    authority.assert_awaited_once()


@pytest.mark.anyio
async def test_consent_receipt_status_does_not_enumerate_another_subject(client: AsyncClient):
    with (
        patch("app.api.v1.consents.get_client_ip", return_value="203.0.113.9"),
        patch(
            "app.api.v1.consents.get_consumer_consent_receipt_status",
            new=AsyncMock(side_effect=HTTPException(status_code=403, detail="consent_authority_denied")),
        ),
    ):
        response = await client.get(
            f"/api/v1/public/consents/{uuid.uuid4()}/status",
            headers={"Authorization": f"Bearer {_token()}"},
        )

    assert response.status_code == 403
    assert response.json()["detail"] == "consent_authority_denied"


@pytest.mark.anyio
async def test_consent_schema_rejects_client_supplied_subject_and_policy_override(client: AsyncClient):
    response = await client.post(
        "/api/v1/public/consents",
        headers={"Authorization": f"Bearer {_token()}"},
        json={
            "purpose": "lead_capture",
            "policy_version": "v2",
            "policy_digest": "a" * 64,
            "idempotency_key": f"grant-{uuid.uuid4()}",
            "consumer_id": str(uuid.uuid4()),
        },
    )

    assert response.status_code == 422


@pytest.mark.anyio
async def test_bound_consumer_grant_requires_durable_visitor_link(client: AsyncClient):
    authority = AsyncMock()
    with (
        patch("app.api.v1.consents.get_client_ip", return_value="203.0.113.9"),
        patch("app.api.v1.consents.enforce_public_consumer_admission", new=AsyncMock()),
        patch("app.api.v1.consents.link_visitor_to_consumer", new=AsyncMock(return_value=False)),
        patch("app.api.v1.consents.grant_consumer_consent_authority", new=authority),
    ):
        response = await client.post(
            "/api/v1/public/consents",
            headers={"Authorization": f"Bearer {_token(consumer_id=uuid.uuid4())}"},
            json={
                "purpose": "lead_capture",
                "policy_version": "v2",
                "policy_digest": "a" * 64,
                "idempotency_key": f"grant-{uuid.uuid4()}",
            },
        )

    assert response.status_code == 403
    assert response.json()["detail"] == "consumer_visitor_subject_denied"
    authority.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("path", ["/api/v1/consumers/lead-capture", "/api/v1/public/leads"])
async def test_lead_compatibility_alias_uses_the_same_scan_authority(path: str, client: AsyncClient):
    consumer_id = uuid.uuid4()
    capture = AsyncMock(
        return_value={
            "outcome": "captured",
            "consumer_id": consumer_id,
            "consent_id": uuid.uuid4(),
            "contact_suppressed": False,
            "created": True,
            "replayed": False,
            "recorded_at": datetime.now(UTC),
        }
    )
    with (
        patch("app.api.v1.consumers.get_client_ip", return_value="203.0.113.9"),
        patch("app.api.v1.consumers.enforce_public_consumer_admission", new=AsyncMock()),
        patch("app.api.v1.consumers._require_consumer_business_plan", new=AsyncMock(return_value=None)),
        patch("app.api.v1.consumers.capture_consumer_lead_authority", new=capture),
    ):
        response = await client.post(
            path,
            headers={"Authorization": f"Bearer {_token()}"},
            json={
                "consent_id": str(uuid.uuid4()),
                "idempotency_key": f"lead-{uuid.uuid4()}",
                "phone": "13800138000",
            },
        )

    assert response.status_code == 201
    assert response.json()["status"] == "captured"
    capture.assert_awaited_once()
