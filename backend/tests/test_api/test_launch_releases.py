"""品牌方上线门禁 API 流程测试。"""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.database import get_db
from app.main import app
from app.middleware.tenant import TenantScopeMiddleware
from app.models.tenant import (
    AgencyAuthorization,
    AgencyAuthStatus,
    Tenant,
    TenantStatus,
    TenantType,
)
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


async def _seed_agency_world(db, agency_tenant_id, client_tenant_id, granted_by_account_id, scope):
    """补齐 acting-context 校验所需的两条 active Tenant + 一条 active 授权。"""
    db.add(
        Tenant(
            id=agency_tenant_id,
            name="测试代运营",
            slug=f"agency-{agency_tenant_id.hex[:8]}",
            status=TenantStatus.active,
            tenant_type=TenantType.agency,
        )
    )
    # launch_facts 只造了 client 业务事实（PageTemplate/Campaign/...），没造 Tenant 行；
    # 中间件的双租户 active 校验需要它存在。
    existing_client = await db.get(Tenant, client_tenant_id)
    if existing_client is None:
        db.add(
            Tenant(
                id=client_tenant_id,
                name="测试品牌客户",
                slug=f"client-{client_tenant_id.hex[:8]}",
                status=TenantStatus.active,
                tenant_type=TenantType.brand,
            )
        )
    await db.flush()
    auth = AgencyAuthorization(
        agency_tenant_id=agency_tenant_id,
        client_tenant_id=client_tenant_id,
        scope=list(scope),
        status=AgencyAuthStatus.active,
        granted_by=granted_by_account_id,
    )
    db.add(auth)
    await db.flush()
    return auth


def _patch_acting_authorization(monkeypatch, db):
    """复刻生产 _load_acting_authorization 的语义，但查测试 db（同引擎）。

    生产逻辑（app/middleware/tenant.py:_load_acting_authorization）：授权存在 + 未过期 +
    双租户 active → 返回 scope 列表；否则 None。这里查同一份测试数据。
    """

    async def _load(_self, agency_tenant_id, client_tenant_id):
        from datetime import UTC, datetime

        from sqlalchemy import select

        authorization = (
            await db.execute(
                select(AgencyAuthorization).where(
                    AgencyAuthorization.agency_tenant_id == uuid.UUID(str(agency_tenant_id)),
                    AgencyAuthorization.client_tenant_id == uuid.UUID(str(client_tenant_id)),
                    AgencyAuthorization.status == AgencyAuthStatus.active,
                )
            )
        ).scalar_one_or_none()
        if authorization is None:
            return None
        expires_at = authorization.expires_at
        if expires_at is not None:
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if expires_at <= datetime.now(UTC):
                return None
        statuses = dict(
            (
                await db.execute(
                    select(Tenant.id, Tenant.status).where(
                        Tenant.id.in_(
                            [
                                uuid.UUID(str(agency_tenant_id)),
                                uuid.UUID(str(client_tenant_id)),
                            ]
                        )
                    )
                )
            ).all()
        )
        if (
            statuses.get(uuid.UUID(str(agency_tenant_id))) != TenantStatus.active
            or statuses.get(uuid.UUID(str(client_tenant_id))) != TenantStatus.active
        ):
            return None
        return list(authorization.scope)

    monkeypatch.setattr(TenantScopeMiddleware, "_load_acting_authorization", _load)


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
async def test_agency_publish_requires_explicit_release_scope(client, db, launch_facts, monkeypatch):
    client_tenant_id, brand_account_id, version, campaign, batch = launch_facts
    agency_tenant_id = uuid.uuid4()
    agency_account_id = uuid.uuid4()
    # 补齐 acting-context 校验所需的两条 active Tenant + active 授权（生产逻辑要求双租户
    # 都 active），并 monkeypatch middleware 改用同一测试 db 查（单连接 SQLite 测试里
    # async_session_factory 是另一个引擎，看不到 fixture 数据）。
    authorization = await _seed_agency_world(db, agency_tenant_id, client_tenant_id, brand_account_id, ["pages"])
    _patch_acting_authorization(monkeypatch, db)

    agency_token = create_access_token(
        str(agency_tenant_id),
        str(agency_account_id),
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
