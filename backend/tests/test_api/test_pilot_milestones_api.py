"""试点里程碑 API 测试（beads: yimatong-bgag.1）。"""

import uuid
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.database import get_db
from app.main import app
from app.models.launch import LaunchRelease, LaunchReleaseStatus
from app.models.scan import ScanEvent
from app.utils.security import create_access_token
from tests.conftest import seed_pilot_tenant


@pytest.fixture
async def client(db):
    async def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


def _headers(tenant_id, account_id, role="admin"):
    token = create_access_token(str(tenant_id), str(account_id), role, tenant_type="brand")
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_get_timeline_200_empty(client, db):
    """已认证 admin：空租户返回 5 个里程碑 + 2 派生时长。"""
    tenant_id = await seed_pilot_tenant(db)
    account_id = uuid.uuid4()

    resp = await client.get("/api/v1/pilot-milestones", headers=_headers(tenant_id, account_id))

    assert resp.status_code == 200
    data = resp.json()
    assert data["tenant_id"] == str(tenant_id)
    assert len(data["milestones"]) == 5
    types = [m["type"] for m in data["milestones"]]
    assert types == ["onboarding", "brand_confirmed", "launched", "first_valid_scan", "first_campaign_published"]
    assert len(data["derived_durations"]) == 2


@pytest.mark.asyncio
async def test_get_timeline_200_with_launched_facts(client, db):
    """有上线 + 扫码事实：里程碑达成并计算派生时长。"""
    onboarding = datetime(2026, 7, 1, tzinfo=UTC)
    launched = datetime(2026, 7, 10, tzinfo=UTC)
    first_scan = datetime(2026, 7, 11, tzinfo=UTC)

    tenant_id = await seed_pilot_tenant(db, created_at=onboarding)
    db.add(
        LaunchRelease(
            tenant_id=tenant_id,
            page_template_id=uuid.uuid4(),
            page_version_id=uuid.uuid4(),
            campaign_id=uuid.uuid4(),
            code_batch_id=uuid.uuid4(),
            status=LaunchReleaseStatus.live,
            readiness_snapshot={},
            content_digest="d" * 64,
            created_by=uuid.uuid4(),
            brand_confirmed_at=datetime(2026, 7, 5, tzinfo=UTC),
            launched_at=launched,
        )
    )
    db.add(
        ScanEvent(
            tenant_id=tenant_id,
            public_id="SCAN001",
            scan_time=first_scan,
            is_valid_visit=True,
        )
    )
    await db.flush()

    resp = await client.get("/api/v1/pilot-milestones", headers=_headers(tenant_id, uuid.uuid4()))

    assert resp.status_code == 200
    by_type = {m["type"]: m for m in resp.json()["milestones"]}
    assert by_type["onboarding"]["status"] == "achieved"
    assert by_type["launched"]["status"] == "achieved"
    assert by_type["first_valid_scan"]["status"] == "achieved"
    # milestone 5 当前事实源未捕获发布时间，按 PRD §4.1 标"未达成"
    assert by_type["first_campaign_published"]["status"] == "not_achieved"

    by_label = {d["label"]: d for d in resp.json()["derived_durations"]}
    assert by_label["开通→上线时长"]["status"] == "computed"
    assert by_label["开通→上线时长"]["seconds"] is not None


@pytest.mark.asyncio
async def test_get_timeline_401_without_token(client, db):
    """无 Authorization 头：401。"""
    await seed_pilot_tenant(db)
    resp = await client.get("/api/v1/pilot-milestones")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_get_timeline_403_insufficient_role(client, db):
    """viewer 角色无 analytics:view 权限：403。"""
    tenant_id = await seed_pilot_tenant(db)
    resp = await client.get("/api/v1/pilot-milestones", headers=_headers(tenant_id, uuid.uuid4(), role="viewer"))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_get_timeline_tenant_isolation(client, db):
    """A 租户只看到自己的里程碑，看不到 B 租户的上线事实。"""
    a_id = await seed_pilot_tenant(db, created_at=datetime(2026, 7, 1, tzinfo=UTC))
    b_id = await seed_pilot_tenant(db, created_at=datetime(2026, 6, 1, tzinfo=UTC))
    # 只有 B 上线
    db.add(
        LaunchRelease(
            tenant_id=b_id,
            page_template_id=uuid.uuid4(),
            page_version_id=uuid.uuid4(),
            campaign_id=uuid.uuid4(),
            code_batch_id=uuid.uuid4(),
            status=LaunchReleaseStatus.live,
            readiness_snapshot={},
            content_digest="d" * 64,
            created_by=uuid.uuid4(),
            launched_at=datetime(2026, 6, 10, tzinfo=UTC),
        )
    )
    await db.flush()

    # A 租户请求：不应看到 launched 达成
    resp = await client.get("/api/v1/pilot-milestones", headers=_headers(a_id, uuid.uuid4()))
    assert resp.status_code == 200
    by_type = {m["type"]: m for m in resp.json()["milestones"]}
    assert by_type["launched"]["status"] == "not_achieved"
    assert by_type["launched"]["achieved_at"] is None
