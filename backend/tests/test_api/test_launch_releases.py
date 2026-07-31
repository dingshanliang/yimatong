"""品牌方上线门禁 API 流程测试。"""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.database import get_db
from app.main import app
from app.models.tenant import AgencyAuthorization, AgencyAuthStatus
from app.utils.security import create_access_token


@pytest.fixture
async def client(db):
    async def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _headers(tenant_id, account_id, role="admin"):
    token = create_access_token(str(tenant_id), str(account_id), role, tenant_type="brand")
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.anyio
async def test_brand_can_check_confirm_and_launch_once(client, launch_facts):
    tenant_id, account_id, version, campaign, batch = launch_facts
    headers = _headers(tenant_id, account_id)

    created = await client.post(
        "/api/v1/launch-releases",
        json={
            "page_version_id": str(version.id),
            "campaign_id": str(campaign.id),
            "code_batch_id": str(batch.id),
        },
        headers=headers,
    )
    assert created.status_code == 201
    release_id = created.json()["id"]
    assert created.json()["status"] == "pending_confirmation"
    assert created.json()["readiness_snapshot"]["ready"] is True

    launched = await client.post(
        f"/api/v1/launch-releases/{release_id}/confirm-and-launch",
        json={"idempotency_key": "api-release-001"},
        headers=headers,
    )
    repeated = await client.post(
        f"/api/v1/launch-releases/{release_id}/confirm-and-launch",
        json={"idempotency_key": "api-release-001"},
        headers=headers,
    )
    assert launched.status_code == 200
    assert repeated.status_code == 200
    assert launched.json()["status"] == "live"
    assert repeated.json()["id"] == release_id


@pytest.mark.anyio
async def test_operator_cannot_confirm_launch(client, launch_facts):
    tenant_id, account_id, version, campaign, batch = launch_facts
    admin_headers = _headers(tenant_id, account_id)
    created = await client.post(
        "/api/v1/launch-releases",
        json={
            "page_version_id": str(version.id),
            "campaign_id": str(campaign.id),
            "code_batch_id": str(batch.id),
        },
        headers=admin_headers,
    )

    operator_headers = _headers(tenant_id, account_id, role="operator")
    response = await client.post(
        f"/api/v1/launch-releases/{created.json()['id']}/confirm-and-launch",
        json={"idempotency_key": "operator-release-001"},
        headers=operator_headers,
    )
    assert response.status_code == 403


@pytest.mark.anyio
async def test_pause_blocks_consumer_page_until_brand_resumes(client, launch_facts):
    tenant_id, account_id, version, campaign, batch = launch_facts
    headers = _headers(tenant_id, account_id)
    created = await client.post(
        "/api/v1/launch-releases",
        json={
            "page_version_id": str(version.id),
            "campaign_id": str(campaign.id),
            "code_batch_id": str(batch.id),
        },
        headers=headers,
    )
    release_id = created.json()["id"]
    await client.post(
        f"/api/v1/launch-releases/{release_id}/confirm-and-launch",
        json={"idempotency_key": "pause-release-001"},
        headers=headers,
    )
    paused = await client.post(
        f"/api/v1/launch-releases/{release_id}/suspend",
        json={"reason": "复核活动配置"},
        headers=headers,
    )
    assert paused.status_code == 200
    assert paused.json()["status"] == "suspended"

    blocked_page = await client.get(f"/api/v1/public/pages/{version.id}", headers=headers)
    assert blocked_page.status_code == 409

    resumed = await client.post(f"/api/v1/launch-releases/{release_id}/resume", headers=headers)
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "live"
    restored_page = await client.get(f"/api/v1/public/pages/{version.id}", headers=headers)
    assert restored_page.status_code == 200


@pytest.mark.anyio
async def test_agency_publish_requires_explicit_release_scope(client, db, launch_facts):
    client_tenant_id, brand_account_id, version, campaign, batch = launch_facts
    agency_tenant_id = uuid.UUID("00000000-0000-0000-0000-000000000777")
    agency_account_id = uuid.UUID("00000000-0000-0000-0000-000000000778")
    authorization = AgencyAuthorization(
        agency_tenant_id=agency_tenant_id,
        client_tenant_id=client_tenant_id,
        scope=["pages"],
        status=AgencyAuthStatus.active,
        granted_by=brand_account_id,
    )
    db.add(authorization)
    await db.flush()

    agency_token = create_access_token(
        agency_tenant_id,
        agency_account_id,
        "operator",
        tenant_type="agency",
        extra={"acting_tenant_id": str(client_tenant_id), "scope": ["pages"]},
    )
    agency_headers = {"Authorization": f"Bearer {agency_token}"}
    prepared = await client.post(
        "/api/v1/ops/launch-releases",
        json={
            "page_version_id": str(version.id),
            "campaign_id": str(campaign.id),
            "code_batch_id": str(batch.id),
        },
        headers=agency_headers,
    )
    assert prepared.status_code == 201
    release_id = prepared.json()["id"]

    brand_headers = _headers(client_tenant_id, brand_account_id)
    confirmed = await client.post(f"/api/v1/launch-releases/{release_id}/confirm", headers=brand_headers)
    assert confirmed.status_code == 200

    denied = await client.post(
        f"/api/v1/ops/launch-releases/{release_id}/publish",
        json={"idempotency_key": "agency-release-denied"},
        headers=agency_headers,
    )
    assert denied.status_code == 403

    authorization.scope = ["pages", "release:execute"]
    await db.flush()
    allowed = await client.post(
        f"/api/v1/ops/launch-releases/{release_id}/publish",
        json={"idempotency_key": "agency-release-allowed"},
        headers=agency_headers,
    )
    assert allowed.status_code == 200
    assert allowed.json()["status"] == "live"
