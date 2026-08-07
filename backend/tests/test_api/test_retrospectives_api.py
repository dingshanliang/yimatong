"""试点复盘 API 测试（beads: yimatong-bgag.2）。"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.database import get_db
from app.main import app
from app.models.retrospective import Retrospective
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


async def _seed_retro(db, tenant_id, period_day=7, launched=None):
    """直接写入一条 pending 复盘，绕过 poller（API 测试聚焦端点行为）。"""
    launched = launched or datetime(2026, 7, 1, tzinfo=UTC)
    retro = Retrospective(
        tenant_id=tenant_id,
        period_day=period_day,
        window_start=launched,
        window_end=launched + timedelta(days=period_day),
        next_review_date=(launched + timedelta(days=period_day + 7)).date(),
        status="pending",
        scorecard_snapshot={"window_days": period_day},
    )
    db.add(retro)
    await db.flush()
    return retro


@pytest.mark.asyncio
async def test_list_200_empty(client, db):
    """已认证 admin：无复盘返回空列表。"""
    tenant_id = await seed_pilot_tenant(db)
    resp = await client.get("/api/v1/retrospectives", headers=_headers(tenant_id, uuid.uuid4()))
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_200_with_retros(client, db):
    """有复盘：返回含派生状态。"""
    tenant_id = await seed_pilot_tenant(db)
    await _seed_retro(db, tenant_id, period_day=7)

    resp = await client.get("/api/v1/retrospectives", headers=_headers(tenant_id, uuid.uuid4()))
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["period_day"] == 7
    assert data[0]["status"] == "pending"
    assert "derived_status" in data[0]
    assert data[0]["scorecard_snapshot"] == {"window_days": 7}
    assert "ops_task_id" in data[0]


@pytest.mark.asyncio
async def test_get_single_404(client, db):
    """不存在的复盘 id：404。"""
    tenant_id = await seed_pilot_tenant(db)
    resp = await client.get(f"/api/v1/retrospectives/{uuid.uuid4()}", headers=_headers(tenant_id, uuid.uuid4()))
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_single_200(client, db):
    """单条复盘详情。"""
    tenant_id = await seed_pilot_tenant(db)
    retro = await _seed_retro(db, tenant_id, period_day=14)

    resp = await client.get(f"/api/v1/retrospectives/{retro.id}", headers=_headers(tenant_id, uuid.uuid4()))
    assert resp.status_code == 200
    assert resp.json()["period_day"] == 14


@pytest.mark.asyncio
async def test_patch_complete_freezes_snapshot(client, db):
    """PATCH mark_completed=true：完成复盘，快照冻结。"""
    tenant_id = await seed_pilot_tenant(db)
    retro = await _seed_retro(db, tenant_id, period_day=7)

    resp = await client.patch(
        f"/api/v1/retrospectives/{retro.id}",
        headers=_headers(tenant_id, uuid.uuid4()),
        json={
            "goal": "提升扫码转化",
            "issues": "转化率偏低",
            "actions": [{"content": "优化权益入口", "status": "pending"}],
            "next_review_date": "2026-08-15",
            "mark_completed": True,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"
    assert data["completed_at"] is not None
    assert data["goal"] == "提升扫码转化"
    assert data["issues"] == "转化率偏低"


@pytest.mark.asyncio
async def test_patch_pending_updates_fields(client, db):
    """PATCH 未 mark_completed：pending 期可更新字段，不推进状态。"""
    tenant_id = await seed_pilot_tenant(db)
    retro = await _seed_retro(db, tenant_id, period_day=7)

    resp = await client.patch(
        f"/api/v1/retrospectives/{retro.id}",
        headers=_headers(tenant_id, uuid.uuid4()),
        json={"goal": "暂存目标", "issues": "初步观察"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "pending"  # 未推进
    assert data["goal"] == "暂存目标"


@pytest.mark.asyncio
async def test_patch_completed_only_allows_supplementary(client, db):
    """已完成复盘：PATCH 只追加 supplementary_notes，不改其他字段。"""
    tenant_id = await seed_pilot_tenant(db)
    retro = await _seed_retro(db, tenant_id, period_day=7)
    # 先完成
    await client.patch(
        f"/api/v1/retrospectives/{retro.id}",
        headers=_headers(tenant_id, uuid.uuid4()),
        json={"issues": "原始问题", "mark_completed": True},
    )
    # 再追加补充
    resp = await client.patch(
        f"/api/v1/retrospectives/{retro.id}",
        headers=_headers(tenant_id, uuid.uuid4()),
        json={"supplementary_notes": "补充说明", "issues": "试图覆盖"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["supplementary_notes"] == "补充说明"
    assert data["issues"] == "原始问题"  # 未被覆盖


@pytest.mark.asyncio
async def test_401_without_token(client, db):
    """无 token：401。"""
    await seed_pilot_tenant(db)
    resp = await client.get("/api/v1/retrospectives")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_403_viewer_cannot_write(client, db):
    """viewer 无 campaign:manage：PATCH 403。"""
    tenant_id = await seed_pilot_tenant(db)
    retro = await _seed_retro(db, tenant_id, period_day=7)
    resp = await client.patch(
        f"/api/v1/retrospectives/{retro.id}",
        headers=_headers(tenant_id, uuid.uuid4(), role="viewer"),
        json={"mark_completed": True},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_tenant_isolation(client, db):
    """A 租户看不到 B 租户的复盘。"""
    a_id = await seed_pilot_tenant(db)
    b_id = await seed_pilot_tenant(db)
    await _seed_retro(db, b_id, period_day=7)  # B 的复盘

    resp = await client.get("/api/v1/retrospectives", headers=_headers(a_id, uuid.uuid4()))
    assert resp.status_code == 200
    assert resp.json() == []  # A 看不到 B 的

    # A 也读不到 B 的复盘详情
    b_retro = (await db.execute(select(Retrospective).where(Retrospective.tenant_id == b_id))).scalar_one()
    resp2 = await client.get(f"/api/v1/retrospectives/{b_retro.id}", headers=_headers(a_id, uuid.uuid4()))
    assert resp2.status_code == 404


@pytest.mark.asyncio
async def test_patch_complete_returns_409_when_carryover_undisposed(client, db):
    """§8：存在未处置的承接动作时，PATCH mark_completed 返回 409（非 500）。

    承接动作未显式选择继续/调整/放弃，客户端可修正后重试。
    """
    tenant_id = await seed_pilot_tenant(db)
    retro = await _seed_retro(db, tenant_id, period_day=14)
    # 直接写入一条未处置的承接动作（模拟 poller 从上期承接）
    retro.actions = [
        {
            "content": "上期遗留动作",
            "owner_id": None,
            "due_date": None,
            "status": "pending",
            "carryover": True,
            "carryover_disposition": None,
        }
    ]
    await db.flush()

    resp = await client.patch(
        f"/api/v1/retrospectives/{retro.id}",
        headers=_headers(tenant_id, uuid.uuid4()),
        json={"mark_completed": True},
    )
    assert resp.status_code == 409
    assert "未显式处置" in resp.json()["detail"]
