import uuid
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.v1 import prd_compat
from app.api.v1.benefit_claims import _claim_scan_token
from app.core.database import get_db
from app.main import app
from app.middleware.request_body_limit import PUBLIC_CONSUMER_JSON_BODY_LIMIT


@pytest.fixture
async def alias_client(monkeypatch):
    async def override_get_db():
        yield object()

    async def claim(request, body, _db):
        return {
            "benefit_id": str(body.benefit_id),
            "phone": body.phone,
            "token": _claim_scan_token(request, body),
        }

    claim_mock = AsyncMock(side_effect=claim)
    monkeypatch.setattr(prd_compat, "claim_benefit_h5", claim_mock)
    app.dependency_overrides[get_db] = override_get_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client, claim_mock
    finally:
        app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_public_claim_alias_is_self_authenticated_and_builds_canonical_request(alias_client):
    client, claim_mock = alias_client
    benefit_id = uuid.uuid4()

    response = await client.post(
        f"/api/v1/public/benefits/{benefit_id}/claim",
        json={"scan_token": "body-scan-token", "phone": "13800138000"},
        headers={"Cookie": "access_token=admin-cookie-must-be-ignored"},
    )

    assert response.status_code == 201
    assert response.json() == {
        "benefit_id": str(benefit_id),
        "phone": "13800138000",
        "token": "body-scan-token",
    }
    assert claim_mock.await_count == 1


@pytest.mark.anyio
async def test_public_claim_alias_prefers_bearer_over_body_token(alias_client):
    client, claim_mock = alias_client
    benefit_id = uuid.uuid4()

    response = await client.post(
        f"/api/v1/public/benefits/{benefit_id}/claim",
        json={"benefit_id": str(benefit_id), "scan_token": "stale-body-token"},
        headers={"Authorization": "Bearer trusted-bearer-token"},
    )

    assert response.status_code == 201
    assert response.json()["token"] == "trusted-bearer-token"
    assert claim_mock.await_count == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("path_id", "body", "expected_status"),
    [
        ("not-a-uuid", {}, 422),
        (str(uuid.uuid4()), {"unexpected": "field"}, 422),
        (str(uuid.uuid4()), {"benefit_id": str(uuid.uuid4())}, 422),
    ],
)
async def test_public_claim_alias_rejects_invalid_or_conflicting_input_before_handler(
    alias_client, path_id, body, expected_status
):
    client, claim_mock = alias_client

    response = await client.post(f"/api/v1/public/benefits/{path_id}/claim", json=body)

    assert response.status_code == expected_status
    claim_mock.assert_not_awaited()


@pytest.mark.anyio
async def test_public_claim_alias_rejects_declared_oversize_before_auth_or_handler(alias_client):
    client, claim_mock = alias_client

    response = await client.post(
        f"/api/v1/public/benefits/{uuid.uuid4()}/claim",
        content=b"x" * (PUBLIC_CONSUMER_JSON_BODY_LIMIT + 1),
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 413
    claim_mock.assert_not_awaited()


@pytest.mark.anyio
async def test_public_claim_alias_rejects_chunked_oversize_before_auth_or_handler(alias_client):
    client, claim_mock = alias_client

    async def oversized_chunks():
        yield b"x" * 10_000
        yield b"y" * (PUBLIC_CONSUMER_JSON_BODY_LIMIT - 9_999)

    response = await client.post(
        f"/api/v1/public/benefits/{uuid.uuid4()}/claim",
        content=oversized_chunks(),
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 413
    claim_mock.assert_not_awaited()
