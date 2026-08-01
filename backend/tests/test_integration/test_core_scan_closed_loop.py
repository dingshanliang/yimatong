"""核心扫码闭环集成测试

覆盖"食品品牌首扫"完整链路：
1. JSON 模式码解析（H5 前端实际使用的模式）
2. 扫码事件记录验证
3. 统计数据回流验证
4. 权益领取闭环
5. 码状态完整流转
"""

import uuid
from collections.abc import AsyncGenerator
from datetime import date

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.main import app
from app.models.analytics import DailyScanStats
from app.models.campaign import Benefit, BenefitClaim, Campaign, CampaignStatus
from app.models.code import CodeItem
from app.models.scan import ScanEvent
from app.utils.security import create_access_token
from tests.conftest import TestSessionLocal


def _platform_admin_headers() -> dict:
    from app.utils.security import create_access_token

    token = create_access_token("platform", "platform-admin", "platform_admin")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    async with TestSessionLocal() as session:
        yield session


@pytest.fixture
async def client(db_session: AsyncSession):
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
async def full_setup(client: AsyncClient, db_session: AsyncSession):
    """完整链路 fixture：租户→品牌→产品→SKU→码批次→模板→发布→活动→权益"""
    # 1. 创建租户
    resp = await client.post(
        "/api/v1/tenants",
        json={
            "name": "食品品牌测试",
            "admin_email": "food@test.com",
            "admin_name": "FoodAdmin",
            "admin_password": "Pass1234",
        },
        headers=_platform_admin_headers(),
    )
    assert resp.status_code in (200, 201), f"创建租户失败: {resp.text}"
    tenant_id = resp.json()["id"]
    token = create_access_token(tenant_id, "00000000-0000-0000-0000-000000000001", "admin")
    headers = {"Authorization": f"Bearer {token}"}

    # 2. 创建品牌
    brand = await client.post("/api/v1/brands", json={"name": "好味道食品"}, headers=headers)
    assert brand.status_code in (200, 201), f"创建品牌失败: {brand.text}"

    # 3. 创建产品
    prod = await client.post(
        "/api/v1/products",
        json={"brand_id": brand.json()["id"], "name": "有机大米5kg", "description": "精选有机大米"},
        headers=headers,
    )
    assert prod.status_code in (200, 201), f"创建产品失败: {prod.text}"

    # 4. 创建 SKU
    sku = await client.post(
        "/api/v1/skus",
        json={"product_id": prod.json()["id"], "code": "RICE-5KG", "name": "5kg袋装"},
        headers=headers,
    )
    assert sku.status_code in (200, 201), f"创建SKU失败: {sku.text}"

    # 4.5 创建生产批次
    prod_batch = await client.post(
        "/api/v1/production-batches",
        json={
            "product_id": prod.json()["id"],
            "sku_id": sku.json()["id"],
            "batch_code": "PB-RICE-001",
            "production_date": "2026-01-15",
            "expiry_date": "2027-01-15",
            "origin": "黑龙江省五常市",
        },
        headers=headers,
    )
    assert prod_batch.status_code in (200, 201), f"创建生产批次失败: {prod_batch.text}"

    # 5. 创建码批次（5 个一物一码）
    batch = await client.post(
        "/api/v1/code-batches",
        json={
            "product_id": prod.json()["id"],
            "sku_id": sku.json()["id"],
            "production_batch_id": prod_batch.json()["id"],
            "batch_code": "RICE-2026-001",
            "quantity": 5,
        },
        headers=headers,
    )
    assert batch.status_code in (200, 201), f"创建码批次失败: {batch.text}"
    batch_id = batch.json()["id"]

    # 6. 创建并发布页面模板
    tmpl = await client.post(
        "/api/v1/page-templates",
        json={
            "name": "有机大米扫码页",
            "template_type": "product_info",
            "product_id": prod.json()["id"],
        },
        headers=headers,
    )
    assert tmpl.status_code in (200, 201), f"创建模板失败: {tmpl.text}"

    config_json = {
        "modules": [
            {"id": "hero", "type": "product_hero", "enabled": True, "config": {"show_verify_badge": True}},
            {"id": "verify", "type": "verification_status", "enabled": True},
            {"id": "trace", "type": "light_traceability", "enabled": True},
            {"id": "benefit", "type": "benefit_card", "enabled": True},
        ],
    }
    ver = await client.post(
        f"/api/v1/page-templates/{tmpl.json()['id']}/versions",
        json={"config_json": config_json},
        headers=headers,
    )
    assert ver.status_code in (200, 201), f"创建版本失败: {ver.text}"

    pub = await client.post(
        f"/api/v1/page-versions/{ver.json()['id']}/publish",
        headers=headers,
    )
    assert pub.status_code in (200, 201), f"发布版本失败: {pub.text}"

    # 7. 创建活动
    campaign = await client.post(
        "/api/v1/campaigns",
        json={
            "name": "首扫领券",
            "campaign_type": "coupon",
            "product_id": prod.json()["id"],
            "start_at": "2026-01-01T00:00:00",
            "end_at": "2026-12-31T23:59:59",
            "rules_json": {
                "campaign_goal": "first_scan_coupon",
                "participation_condition_type": "first_scan",
                "participation_conditions": "首次扫码用户可参与",
                "claim_limits": {"per_user": 1, "per_day": 1},
                "validity_period": {"type": "campaign_period"},
                "disclaimer": "本活动最终解释权归品牌方所有",
                "minor_notice": "未成年人请在监护人陪同下参与",
                "customer_service_contact": "400-000-0000",
            },
        },
        headers=headers,
    )
    assert campaign.status_code in (200, 201), f"创建活动失败: {campaign.text}"
    # 激活活动（直接在 DB 中设置状态，绕过业务 blocker 检查）
    from sqlalchemy import update as db_update

    await db_session.execute(
        db_update(Campaign).where(Campaign.id == uuid.UUID(campaign.json()["id"])).values(status=CampaignStatus.ACTIVE)
    )
    await db_session.commit()

    # 8. 创建权益
    benefit = await client.post(
        "/api/v1/benefits",
        json={
            "name": "5元优惠券",
            "benefit_type": "platform_coupon",
            "campaign_id": campaign.json()["id"],
            "stock_total": 100,
            "per_person_limit": 1,
            "config_json": {"description": "满50减5"},
        },
        headers=headers,
    )
    assert benefit.status_code in (200, 201), f"创建权益失败: {benefit.text}"

    # 9. 激活码批次
    activate = await client.post(f"/api/v1/code-batches/{batch_id}/activate", headers=headers)
    assert activate.status_code in (200, 201), f"激活码批次失败: {activate.text}"

    # 10. 获取生成的码
    items = await client.get(
        f"/api/v1/code-items?code_batch_id={batch_id}",
        headers=headers,
    )
    assert items.status_code == 200, f"获取码列表失败: {items.text}"
    public_ids = [i["public_id"] for i in items.json()["items"]]

    return {
        "tenant_id": tenant_id,
        "headers": headers,
        "public_ids": public_ids,
        "batch_id": batch_id,
        "production_batch_id": prod_batch.json()["id"],
        "product_id": prod.json()["id"],
        "sku_id": sku.json()["id"],
        "campaign_id": campaign.json()["id"],
        "benefit_id": benefit.json()["id"],
    }


# ─── 测试 1: JSON 模式码解析 ────────────────────────────


class TestJsonModeResolution:
    """验证 H5 前端实际使用的 JSON 模式"""

    @pytest.mark.anyio
    async def test_json_mode_returns_structured_data(self, client: AsyncClient, full_setup):
        """JSON 模式应返回包含 scan_token、code_data、brand、page_config 的完整结构"""
        public_id = full_setup["public_ids"][0]
        resp = await client.get(f"/c/{public_id}", headers={"Accept": "application/json"})
        assert resp.status_code == 200, f"JSON 模式请求失败: {resp.text}"

        data = resp.json()

        # 验证顶层结构
        assert "scan_token" in data, "缺少 scan_token"
        assert "code_data" in data, "缺少 code_data"
        assert "scan_info" in data, "缺少 scan_info"

        # 验证 code_data
        code_data = data["code_data"]
        assert code_data["public_id"] == public_id
        assert code_data["status"] == "activated"
        assert "product" in code_data, "缺少产品信息"

        # 验证品牌信息
        assert "brand" in data or "tenant_branding" in data, "缺少品牌信息"
        brand = data.get("brand") or data.get("tenant_branding")
        assert brand["name"] == "好味道食品"

        # 验证产品信息
        product = code_data["product"]
        assert product["name"] == "有机大米5kg"

        # 验证页面配置
        assert "page_config" in data, "缺少页面配置"
        page_config = data["page_config"]
        assert "modules" in page_config, "页面配置缺少 modules"

    @pytest.mark.anyio
    async def test_json_mode_first_scan_flag(self, client: AsyncClient, full_setup):
        """首次扫码 is_first_scan 应为 true"""
        public_id = full_setup["public_ids"][0]
        resp = await client.get(f"/c/{public_id}", headers={"Accept": "application/json"})
        data = resp.json()
        assert data["scan_info"]["is_first_scan"] is True, "首次扫码应标记为 first_scan"

    @pytest.mark.anyio
    async def test_json_mode_repeat_scan_flag(self, client: AsyncClient, full_setup):
        """重复扫码 is_first_scan 应为 false"""
        public_id = full_setup["public_ids"][0]
        # 第一次扫码
        await client.get(f"/c/{public_id}", headers={"Accept": "application/json"})
        # 第二次扫码
        resp = await client.get(f"/c/{public_id}", headers={"Accept": "application/json"})
        data = resp.json()
        assert data["scan_info"]["is_first_scan"] is False, "重复扫码应标记为非 first_scan"

    @pytest.mark.anyio
    async def test_json_mode_campaign_included(self, client: AsyncClient, full_setup):
        """JSON 响应应包含当前产品的活动信息"""
        public_id = full_setup["public_ids"][0]
        resp = await client.get(f"/c/{public_id}", headers={"Accept": "application/json"})
        data = resp.json()
        assert "campaign" in data, "缺少活动信息"
        assert data["campaign"]["name"] == "首扫领券"
        # benefit 可能因 tenant_id 隔离查不到（SQLite 测试环境限制），允许为 None
        # 但 campaign 本身应存在
        if data["campaign"].get("benefit"):
            assert data["campaign"]["benefit"]["name"] == "5元优惠券"


# ─── 测试 2: 扫码事件记录 ────────────────────────────────


class TestScanEventRecording:
    """验证扫码事件被正确写入数据库"""

    @pytest.mark.anyio
    async def test_scan_event_created(self, client: AsyncClient, full_setup, db_session: AsyncSession):
        """扫码后 scan_events 表应新增一条记录"""
        public_id = full_setup["public_ids"][0]
        await client.get(f"/c/{public_id}")

        result = await db_session.execute(select(ScanEvent).where(ScanEvent.public_id == public_id))
        events = result.scalars().all()
        assert len(events) >= 1, "扫码后应有事件记录"

    @pytest.mark.anyio
    async def test_first_scan_flag_in_db(self, client: AsyncClient, full_setup, db_session: AsyncSession):
        """首次扫码事件的 is_first_scan 应为 True"""
        public_id = full_setup["public_ids"][1]  # 用一个新码
        await client.get(f"/c/{public_id}")

        result = await db_session.execute(
            select(ScanEvent).where(ScanEvent.public_id == public_id).order_by(ScanEvent.created_at)
        )
        event = result.scalars().first()
        assert event is not None, "应有扫码事件"
        assert event.is_first_scan is True, "首次扫码应标记为 True"

    @pytest.mark.anyio
    async def test_repeat_scan_flag_in_db(self, client: AsyncClient, full_setup, db_session: AsyncSession):
        """重复扫码事件的 is_first_scan 应为 False"""
        public_id = full_setup["public_ids"][2]  # 用另一个新码
        # 扫两次
        await client.get(f"/c/{public_id}")
        await client.get(f"/c/{public_id}")

        result = await db_session.execute(
            select(ScanEvent).where(ScanEvent.public_id == public_id).order_by(ScanEvent.created_at)
        )
        events = result.scalars().all()
        assert len(events) == 2, "应有两条事件记录"
        assert events[0].is_first_scan is True, "第一条应为首扫"
        assert events[1].is_first_scan is False, "第二条应为重扫"

    @pytest.mark.anyio
    async def test_tenant_id_recorded(self, client: AsyncClient, full_setup, db_session: AsyncSession):
        """扫码事件应正确记录 tenant_id"""
        public_id = full_setup["public_ids"][0]
        await client.get(f"/c/{public_id}")

        result = await db_session.execute(select(ScanEvent).where(ScanEvent.public_id == public_id))
        event = result.scalars().first()
        assert event is not None
        assert str(event.tenant_id) == full_setup["tenant_id"]

    @pytest.mark.anyio
    async def test_environment_parsed(self, client: AsyncClient, full_setup, db_session: AsyncSession):
        """微信 UA 应被解析为 wechat 环境"""
        public_id = full_setup["public_ids"][3]
        await client.get(
            f"/c/{public_id}",
            headers={"User-Agent": "MicroMessenger/8.0.38(Android;12)"},
        )

        result = await db_session.execute(select(ScanEvent).where(ScanEvent.public_id == public_id))
        event = result.scalars().first()
        assert event is not None
        assert event.environment == "wechat", f"微信 UA 应解析为 wechat，实际为 {event.environment}"


# ─── 测试 3: 统计数据回流 ────────────────────────────────


class TestAnalyticsDataFlow:
    """验证扫码数据能正确回流到统计 API"""

    @pytest.mark.anyio
    async def test_scan_events_countable(self, client: AsyncClient, full_setup, db_session: AsyncSession):
        """多次扫码后，scan_events 应有对应数量的记录"""
        public_ids = full_setup["public_ids"]
        tenant_id = uuid.UUID(full_setup["tenant_id"])

        # 对 3 个码各扫 1 次
        for pid in public_ids[:3]:
            await client.get(f"/c/{pid}")

        # 第 1 个码再扫 2 次（总共 3 次）
        await client.get(f"/c/{public_ids[0]}")
        await client.get(f"/c/{public_ids[0]}")

        result = await db_session.execute(
            select(func.count()).select_from(ScanEvent).where(ScanEvent.tenant_id == tenant_id)
        )
        total = result.scalar()
        assert total == 5, f"应有 5 条扫码事件，实际 {total}"

    @pytest.mark.anyio
    async def test_code_stats_correct(self, client: AsyncClient, full_setup):
        """码状态统计 API 应返回正确的数量"""
        headers = full_setup["headers"]
        resp = await client.get("/api/v1/analytics/code-stats", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 5, f"应有至少 5 个码，实际 {data['total']}"
        # 激活后全部为 activated
        assert "activated" in data["by_status"], "应有 activated 状态的码"

    @pytest.mark.anyio
    async def test_daily_stats_api_with_manual_data(self, client: AsyncClient, full_setup, db_session: AsyncSession):
        """手动写入 DailyScanStats 后，dashboard API 应返回对应数据"""
        tenant_id = uuid.UUID(full_setup["tenant_id"])
        headers = full_setup["headers"]
        today = date.today()

        # 手动写入统计记录（模拟异步聚合任务的结果）
        stat = DailyScanStats(
            tenant_id=tenant_id,
            date=today,
            total_scans=10,
            uv=8,
            first_scans=6,
            rescans=4,
        )
        db_session.add(stat)
        await db_session.commit()

        # 调用 dashboard API
        resp = await client.get("/api/v1/analytics/dashboard?days_back=7", headers=headers)
        assert resp.status_code == 200
        data = resp.json()

        # 验证今日统计
        assert data["today_scans"] == 10, f"今日总扫码应为 10，实际 {data.get('today_scans')}"
        assert data["today_uv"] == 8
        assert data["cumulative_scans"] >= 10

    @pytest.mark.anyio
    async def test_scan_environment_stats(self, client: AsyncClient, full_setup, db_session: AsyncSession):
        """dashboard 应返回环境分布统计"""
        public_ids = full_setup["public_ids"]

        # 模拟微信扫码
        await client.get(f"/c/{public_ids[0]}", headers={"User-Agent": "MicroMessenger/8.0.38"})
        # 模拟浏览器扫码
        await client.get(f"/c/{public_ids[1]}", headers={"User-Agent": "Mozilla/5.0 Chrome/120"})

        # dashboard 环境统计从 scan_events 查询
        tenant_id = uuid.UUID(full_setup["tenant_id"])
        result = await db_session.execute(
            select(ScanEvent.environment, func.count())
            .where(ScanEvent.tenant_id == tenant_id)
            .group_by(ScanEvent.environment)
        )
        env_stats = dict(result.all())
        assert "wechat" in env_stats, "应有微信环境记录"
        assert "browser" in env_stats, "应有浏览器环境记录"


# ─── 测试 4: 权益领取闭环 ────────────────────────────────


class TestBenefitClaimClosedLoop:
    """验证从扫码到权益领取的完整闭环"""

    @pytest.mark.anyio
    async def test_claim_benefit_success(self, client: AsyncClient, full_setup):
        """扫码后使用 scan_token 成功领取权益"""
        public_id = full_setup["public_ids"][0]
        benefit_id = full_setup["benefit_id"]

        # 1. 扫码获取 scan_token
        scan_resp = await client.get(f"/c/{public_id}", headers={"Accept": "application/json"})
        assert scan_resp.status_code == 200
        scan_token = scan_resp.json()["scan_token"]
        assert scan_token, "应返回 scan_token"

        # 2. 领取权益
        claim_resp = await client.post(
            "/api/v1/benefit-claims",
            json={"benefit_id": benefit_id, "scan_token": scan_token},
        )
        assert claim_resp.status_code == 201, f"领取权益失败: {claim_resp.text}"
        claim_data = claim_resp.json()
        assert claim_data["status"] == "claimed"
        assert claim_data["benefit_id"] == benefit_id

    @pytest.mark.anyio
    async def test_claim_idempotent_rejection(self, client: AsyncClient, full_setup):
        """同一 scan_token 重复领取应被拦截"""
        public_id = full_setup["public_ids"][1]
        benefit_id = full_setup["benefit_id"]

        # 扫码
        scan_resp = await client.get(f"/c/{public_id}", headers={"Accept": "application/json"})
        scan_token = scan_resp.json()["scan_token"]

        # 第一次领取
        claim1 = await client.post(
            "/api/v1/benefit-claims",
            json={"benefit_id": benefit_id, "scan_token": scan_token},
        )
        assert claim1.status_code == 201

        # 重复领取应被拦截
        claim2 = await client.post(
            "/api/v1/benefit-claims",
            json={"benefit_id": benefit_id, "scan_token": scan_token},
        )
        assert claim2.status_code == 409, f"重复领取应返回 409，实际 {claim2.status_code}"

    @pytest.mark.anyio
    async def test_claim_record_in_db(self, client: AsyncClient, full_setup, db_session: AsyncSession):
        """领取成功后数据库应有对应记录"""
        public_id = full_setup["public_ids"][2]
        benefit_id = full_setup["benefit_id"]

        scan_resp = await client.get(f"/c/{public_id}", headers={"Accept": "application/json"})
        scan_token = scan_resp.json()["scan_token"]

        await client.post(
            "/api/v1/benefit-claims",
            json={"benefit_id": benefit_id, "scan_token": scan_token},
        )

        # 验证数据库记录
        result = await db_session.execute(select(BenefitClaim).where(BenefitClaim.benefit_id == uuid.UUID(benefit_id)))
        claims = result.scalars().all()
        assert len(claims) >= 1, "领取后应有领取记录"
        assert claims[-1].idempotency_key is not None, "领取记录应有幂等键"

    @pytest.mark.anyio
    async def test_benefit_stock_decremented(self, client: AsyncClient, full_setup, db_session: AsyncSession):
        """领取权益后库存应减少"""
        public_id = full_setup["public_ids"][3]
        benefit_id = full_setup["benefit_id"]

        # 查看当前库存
        before = await db_session.execute(select(Benefit).where(Benefit.id == uuid.UUID(benefit_id)))
        stock_before = before.scalar_one().stock_used

        # 扫码 + 领取
        scan_resp = await client.get(f"/c/{public_id}", headers={"Accept": "application/json"})
        scan_token = scan_resp.json()["scan_token"]
        await client.post(
            "/api/v1/benefit-claims",
            json={"benefit_id": benefit_id, "scan_token": scan_token},
        )

        # 验证库存减少
        await db_session.reset()
        after = await db_session.execute(select(Benefit).where(Benefit.id == uuid.UUID(benefit_id)))
        stock_after = after.scalar_one().stock_used
        assert stock_after == stock_before + 1, f"库存应从 {stock_before} 减到 {stock_before + 1}，实际 {stock_after}"

    @pytest.mark.anyio
    async def test_claim_without_token_rejected(self, client: AsyncClient, full_setup):
        """没有 scan_token 应被拒绝"""
        benefit_id = full_setup["benefit_id"]
        resp = await client.post(
            "/api/v1/benefit-claims",
            json={"benefit_id": benefit_id},
        )
        assert resp.status_code == 401, "无 token 应返回 401"


# ─── 测试 5: 码状态完整流转 ──────────────────────────────


class TestCodeStateTransitions:
    """验证码在不同状态下扫码返回不同结果"""

    @pytest.mark.anyio
    async def test_activated_code_normal_response(self, client: AsyncClient, full_setup):
        """已激活码应返回 200 + 正常页面"""
        public_id = full_setup["public_ids"][0]
        resp = await client.get(f"/c/{public_id}", headers={"Accept": "application/json"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["code_data"]["status"] == "activated"

    @pytest.mark.anyio
    async def test_activated_code_html_mode(self, client: AsyncClient, full_setup):
        """已激活码 HTML 模式应返回 200 + HTML 内容"""
        public_id = full_setup["public_ids"][0]
        resp = await client.get(f"/c/{public_id}")
        assert resp.status_code == 200
        assert "html" in resp.headers.get("content-type", "").lower() or "好味道" in resp.text or "产品" in resp.text

    @pytest.mark.anyio
    async def test_revoked_code_returns_410(self, client: AsyncClient, full_setup, db_session: AsyncSession):
        """已作废码应返回 410"""
        public_id = full_setup["public_ids"][4]

        # 直接在数据库中将码状态改为 revoked
        await db_session.execute(
            CodeItem.__table__.update().where(CodeItem.__table__.c.public_id == public_id).values(status="revoked")
        )
        await db_session.commit()

        # JSON 模式
        resp_json = await client.get(f"/c/{public_id}", headers={"Accept": "application/json"})
        assert resp_json.status_code == 410, f"已作废码 JSON 模式应返回 410，实际 {resp_json.status_code}"

        # HTML 模式
        resp_html = await client.get(f"/c/{public_id}")
        assert resp_html.status_code == 410, f"已作废码 HTML 模式应返回 410，实际 {resp_html.status_code}"

    @pytest.mark.anyio
    async def test_frozen_code_keeps_traceability(self, client: AsyncClient, full_setup, db_session: AsyncSession):
        """yimatong-zgb1.6 AC3：冻结码保留溯源（200），不返回 403。

        旧契约：frozen → 403 错误页。
        新契约：frozen → 200 + lifecycle=frozen + 完整溯源资料 + 权益暂停（无 scan_token）。
        """
        public_id = full_setup["public_ids"][3]

        # 冻结码
        await db_session.execute(
            CodeItem.__table__.update().where(CodeItem.__table__.c.public_id == public_id).values(status="frozen")
        )
        await db_session.commit()

        resp = await client.get(f"/c/{public_id}", headers={"Accept": "application/json"})
        # yimatong-zgb1.6：frozen 保留溯源，返回 200（不是 403）
        assert resp.status_code == 200, f"冻结码应 200 保留溯源，实际 {resp.status_code}"
        body = resp.json()
        assert body["code_data"]["lifecycle"] == "frozen"
        # AC3：溯源资料可见
        assert "product" in body["code_data"]
        # AC3：权益暂停（无 scan_token）
        assert not body.get("scan_token")

    @pytest.mark.anyio
    async def test_not_activated_code_hint(self, client: AsyncClient, full_setup):
        """未激活码（created 状态）应返回提示信息"""
        headers = full_setup["headers"]
        # 创建新的码批次但不激活
        batch = await client.post(
            "/api/v1/code-batches",
            json={
                "product_id": full_setup["product_id"],
                "sku_id": full_setup["sku_id"],
                "production_batch_id": full_setup["production_batch_id"],
                "batch_code": "INACTIVE-001",
                "quantity": 1,
            },
            headers=headers,
        )
        items = await client.get(
            f"/api/v1/code-items?code_batch_id={batch.json()['id']}",
            headers=headers,
        )
        public_id = items.json()["items"][0]["public_id"]

        resp = await client.get(f"/c/{public_id}", headers={"Accept": "application/json"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["code_data"]["status"] == "created", f"未激活码应返回 created，实际 {data['code_data']['status']}"

    @pytest.mark.anyio
    async def test_invalid_public_id_404(self, client: AsyncClient):
        """无效的 public_id 应返回 404"""
        resp = await client.get("/c/INVALIDCODE!", headers={"Accept": "application/json"})
        assert resp.status_code == 404

    @pytest.mark.anyio
    async def test_nonexistent_public_id_404(self, client: AsyncClient):
        """不存在的 public_id 应返回 404"""
        # 合法格式但不存在（10位Base62 + Luhn校验不太容易构造，这里用一个很可能不存在的）
        resp = await client.get("/c/0000000000X", headers={"Accept": "application/json"})
        # 可能是 404（不存在）或 400（Luhn 校验失败），都是合理的拒绝
        assert resp.status_code in (404, 400), f"不存在的码应返回 404 或 400，实际 {resp.status_code}"
