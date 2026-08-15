"""品牌方上线门禁 API 流程测试。"""

import uuid
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.core.database import get_db
from app.main import app
from app.middleware.tenant import TenantScopeMiddleware
from app.models.campaign import Benefit, BenefitClaim, Campaign
from app.models.code import CodeItem
from app.models.scan import ScanEvent
from app.models.tenant import (
    AgencyAuthorization,
    AgencyAuthStatus,
    Tenant,
    TenantStatus,
    TenantType,
)
from app.services.public_id import generate_public_id
from app.services.scan_token import create_scan_token, verify_scan_token
from app.utils.client_ip import compute_ip_hash
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


@pytest.fixture
async def committing_client(db):
    async def override_get_db():
        try:
            yield db
            await db.commit()
        except Exception:
            await db.rollback()
            raise

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
async def test_brand_can_check_confirm_and_launch_once(client, db, launch_facts, monkeypatch):
    tenant_id, account_id, version, campaign, batch = launch_facts
    headers = _headers(tenant_id, account_id)
    item = await db.scalar(select(CodeItem).where(CodeItem.tenant_id == tenant_id, CodeItem.code_batch_id == batch.id))
    public_id = generate_public_id()
    original_public_id = item.public_id
    item.public_id = public_id
    scan_event = await db.scalar(
        select(ScanEvent).where(ScanEvent.tenant_id == tenant_id, ScanEvent.public_id == original_public_id)
    )
    scan_event.public_id = public_id
    live_benefit = Benefit(
        tenant_id=tenant_id,
        campaign_id=campaign.id,
        name="已上线活动权益",
        benefit_type="platform_coupon",
        config_json={},
        stock_total=5,
        stock_used=0,
        per_person_limit=1,
        status="active",
    )
    db.add(live_benefit)
    await db.flush()
    monkeypatch.setattr(
        "app.api.v1.resolver._record_scan",
        AsyncMock(return_value={"scan_event_id": str(scan_event.id), "is_first_scan": False}),
    )

    paused = await client.get(f"/c/{public_id}", headers={"Accept": "application/json"})
    assert paused.status_code == 200
    assert paused.json()["scan_token"] is None
    assert paused.json()["scan_info"]["paused_reason"] == "launch_not_live"
    assert "campaign" not in paused.json()

    created = await client.post(
        "/api/v1/launch-releases",
        json={
            "page_version_id": str(version.id),
            "campaign_id": str(campaign.id),
            "code_batch_id": str(batch.id),
            "idempotency_key": "create-brand-release-001",
        },
        headers=headers,
    )
    assert created.status_code == 201
    release_id = created.json()["id"]
    assert created.json()["status"] == "pending_confirmation"
    assert created.json()["readiness_snapshot"]["ready"] is True
    assert created.json()["readiness_sample_code"] == {
        "public_id": public_id,
        "status": "activated",
        "ready": True,
    }

    listed = await client.get("/api/v1/launch-releases?page=1&page_size=1", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert [item["id"] for item in listed.json()["items"]] == [release_id]
    invalid_page = await client.get("/api/v1/launch-releases?page_size=101", headers=headers)
    assert invalid_page.status_code == 422

    confirmed = await client.post(
        f"/api/v1/launch-releases/{release_id}/confirm",
        json={"idempotency_key": "confirm-release-001"},
        headers=headers,
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "confirmed"
    launched = await client.post(
        f"/api/v1/launch-releases/{release_id}/launch",
        json={"idempotency_key": "api-release-001"},
        headers=headers,
    )
    repeated = await client.post(
        f"/api/v1/launch-releases/{release_id}/launch",
        json={"idempotency_key": "api-release-001"},
        headers=headers,
    )
    assert launched.status_code == 200
    assert repeated.status_code == 200
    assert launched.json()["status"] == "live"
    assert repeated.json()["id"] == release_id

    resolved = await client.get(f"/c/{public_id}", headers={"Accept": "application/json"})
    scan_token = resolved.json()["scan_token"]
    payload = verify_scan_token(scan_token, public_id)
    assert payload is not None
    assert payload["tenant_id"] == str(tenant_id)
    assert payload["version"] == 2
    assert payload["launch_release_id"] == release_id
    assert payload["campaign_id"] == str(campaign.id)
    assert payload["code_batch_id"] == str(batch.id)
    assert payload["content_digest"] == launched.json()["content_digest"]
    assert resolved.json()["campaign"]["id"] == str(campaign.id)

    foreign_campaign = Campaign(
        tenant_id=tenant_id,
        product_id=campaign.product_id,
        name="未纳入上线版本的活动",
        campaign_type="scan",
        status="active",
        start_at=campaign.start_at,
        end_at=campaign.end_at,
        rules_json={},
    )
    db.add(foreign_campaign)
    await db.flush()
    foreign_benefit = Benefit(
        tenant_id=tenant_id,
        campaign_id=foreign_campaign.id,
        name="未纳入上线版本的权益",
        benefit_type="platform_coupon",
        config_json={},
        stock_total=5,
        stock_used=0,
        per_person_limit=1,
        status="active",
    )
    db.add(foreign_benefit)
    await db.flush()

    foreign_claim = await client.post(
        "/api/v1/benefit-claims",
        json={"benefit_id": str(foreign_benefit.id), "scan_token": scan_token},
    )
    assert foreign_claim.status_code == 409
    assert foreign_claim.json()["detail"]["code"] == "benefit_not_in_launch_release"
    assert foreign_benefit.stock_used == 0

    legacy_token = create_scan_token(
        public_id,
        compute_ip_hash("127.0.0.1"),
        tenant_id=str(tenant_id),
        scan_event_id=payload["scan_event_id"],
        visitor_id=payload["visitor_id"],
    )
    legacy_claim = await client.post(
        "/api/v1/benefit-claims",
        json={"benefit_id": str(live_benefit.id), "scan_token": legacy_token},
    )
    assert legacy_claim.status_code == 401
    assert legacy_claim.json()["detail"]["code"] == "scan_token_unbound"
    assert live_benefit.stock_used == 0

    authority_race = AsyncMock(return_value={"outcome": "launch_release_not_current", "created": False})
    monkeypatch.setattr("app.services.campaign.claim_benefit", authority_race)
    stale_race = await client.post(
        "/api/v1/benefit-claims",
        json={"benefit_id": str(live_benefit.id), "scan_token": scan_token},
    )
    assert stale_race.status_code == 409
    assert stale_race.json()["detail"]["code"] == "launch_release_not_current"
    authority_race.return_value = {"outcome": "benefit_not_in_launch_release", "created": False}
    campaign_race = await client.post(
        "/api/v1/benefit-claims",
        json={"benefit_id": str(live_benefit.id), "scan_token": scan_token},
    )
    assert campaign_race.status_code == 409
    assert campaign_race.json()["detail"]["code"] == "benefit_not_in_launch_release"
    assert authority_race.await_count == 2
    assert live_benefit.stock_used == 0

    suspended = await client.post(
        f"/api/v1/launch-releases/{release_id}/suspend",
        json={"idempotency_key": "suspend-old-token", "reason": "暂停旧凭证领取"},
        headers=headers,
    )
    assert suspended.status_code == 200

    stale_claim = await client.post(
        "/api/v1/benefit-claims",
        json={"benefit_id": str(live_benefit.id), "scan_token": scan_token},
    )
    assert stale_claim.status_code == 409
    assert stale_claim.json()["detail"]["code"] == "launch_release_not_current"
    assert live_benefit.stock_used == 0
    assert await db.scalar(select(func.count()).select_from(BenefitClaim)) == 0


@pytest.mark.anyio
async def test_claim_with_a_pre_drift_token_invalidates_release_and_writes_nothing(
    committing_client, db, launch_facts, monkeypatch
):
    from app.models.connector import BenefitDelivery
    from app.services.launch import confirm_launch_release, create_launch_release, launch_confirmed_release

    tenant_id, account_id, version, campaign, batch = launch_facts
    item = await db.scalar(select(CodeItem).where(CodeItem.tenant_id == tenant_id, CodeItem.code_batch_id == batch.id))
    event = await db.scalar(
        select(ScanEvent).where(ScanEvent.tenant_id == tenant_id, ScanEvent.public_id == item.public_id)
    )
    public_id = generate_public_id()
    item.public_id = public_id
    event.public_id = public_id
    benefit = Benefit(
        tenant_id=tenant_id,
        campaign_id=campaign.id,
        name="上线活动权益",
        benefit_type="platform_coupon",
        config_json={"coupon_name": "上线券"},
        stock_total=5,
        stock_used=0,
        per_person_limit=1,
        status="active",
    )
    db.add(benefit)
    await db.flush()
    release = await create_launch_release(
        db,
        tenant_id,
        account_id,
        page_version_id=version.id,
        campaign_id=campaign.id,
        code_batch_id=batch.id,
    )
    await confirm_launch_release(db, release, account_id, "confirm-config-drift")
    await launch_confirmed_release(db, release, account_id, "launch-config-drift")
    monkeypatch.setattr(
        "app.api.v1.resolver._record_scan",
        AsyncMock(return_value={"scan_event_id": str(event.id), "is_first_scan": False}),
    )
    resolved = await committing_client.get(f"/c/{public_id}", headers={"Accept": "application/json"})
    assert resolved.status_code == 200, resolved.text
    token = resolved.json()["scan_token"]
    assert token

    benefit.config_json = {"coupon_name": "被修改的券"}
    await db.flush()
    claimed = await committing_client.post(
        "/api/v1/benefit-claims",
        json={"benefit_id": str(benefit.id), "scan_token": token},
    )

    assert claimed.status_code == 409
    assert claimed.json()["detail"]["code"] == "launch_release_not_current"
    await db.refresh(release)
    await db.refresh(benefit)
    assert release.status == "invalidated"
    assert benefit.stock_used == 0
    assert await db.scalar(select(func.count()).select_from(BenefitClaim)) == 0
    assert await db.scalar(select(func.count()).select_from(BenefitDelivery)) == 0


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
            "idempotency_key": "create-operator-denied-001",
        },
        headers=admin_headers,
    )

    operator_headers = _headers(tenant_id, account_id, role="operator")
    response = await client.post(
        f"/api/v1/launch-releases/{created.json()['id']}/confirm",
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
            "idempotency_key": "create-pause-release-001",
        },
        headers=headers,
    )
    release_id = created.json()["id"]
    await client.post(
        f"/api/v1/launch-releases/{release_id}/confirm",
        json={"idempotency_key": "confirm-pause-release-001"},
        headers=headers,
    )
    await client.post(
        f"/api/v1/launch-releases/{release_id}/launch",
        json={"idempotency_key": "launch-pause-release-001"},
        headers=headers,
    )
    paused = await client.post(
        f"/api/v1/launch-releases/{release_id}/suspend",
        json={"reason": "复核活动配置", "idempotency_key": "suspend-release-001"},
        headers=headers,
    )
    assert paused.status_code == 200
    assert paused.json()["status"] == "suspended"

    blocked_page = await client.get(f"/api/v1/public/pages/{version.id}", headers=headers)
    assert blocked_page.status_code == 409

    combined = await client.post(
        f"/api/v1/launch-releases/{release_id}/confirm-and-launch",
        json={"idempotency_key": "cannot-bypass-suspension"},
        headers=headers,
    )
    assert combined.status_code == 409

    resumed = await client.post(
        f"/api/v1/launch-releases/{release_id}/resume",
        json={"idempotency_key": "resume-release-001"},
        headers=headers,
    )
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
            "idempotency_key": "agency-create-release-001",
        },
        headers=agency_headers,
    )
    assert prepared.status_code == 201
    release_id = prepared.json()["id"]

    brand_headers = _headers(client_tenant_id, brand_account_id)
    confirmed = await client.post(
        f"/api/v1/launch-releases/{release_id}/confirm",
        json={"idempotency_key": "agency-brand-confirm-001"},
        headers=brand_headers,
    )
    assert confirmed.status_code == 200

    denied = await client.post(
        f"/api/v1/ops/launch-releases/{release_id}/publish",
        json={"idempotency_key": "agency-release-denied"},
        headers=agency_headers,
    )
    assert denied.status_code == 403

    authorization.scope = ["pages", "release:execute"]
    await db.flush()
    agency_token = create_access_token(
        str(agency_tenant_id),
        str(agency_account_id),
        "operator",
        tenant_type="agency",
        extra={"acting_tenant_id": str(client_tenant_id), "scope": ["pages", "release:execute"]},
    )
    agency_headers = {"Authorization": f"Bearer {agency_token}"}
    allowed = await client.post(
        f"/api/v1/ops/launch-releases/{release_id}/publish",
        json={"idempotency_key": "agency-release-allowed"},
        headers=agency_headers,
    )
    assert allowed.status_code == 200
    assert allowed.json()["status"] == "live"
