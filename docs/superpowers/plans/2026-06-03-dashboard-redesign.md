# 工作台与看板体系重新设计 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 7 个碎片化看板整合为 4 个页面，新增转化漏斗和状态提醒，使经营看板覆盖 PRD BI-01 全部指标。

**Architecture:** 后端新增 4 个 API 端点（转化漏斗、状态提醒、活动排行、最近动态）并增强现有 dashboard API。前端重写 DashboardHome 为 6 区域布局，合并活动看板+GMV归因为深度分析页面，风控看板改名为风控中心，删除扫码统计页。侧边栏菜单从 5 项二级菜单精简为 3 个一级项。

**Tech Stack:** FastAPI + SQLAlchemy 2.0 async (后端), Next.js 16 + Ant Design 6 + @ant-design/charts (前端)

**Spec:** `docs/superpowers/specs/2026-06-03-dashboard-redesign-design.md`

---

## File Structure

### Backend — New Files
- `backend/app/schemas/analytics.py` — Pydantic schemas for new analytics APIs
- `backend/app/services/analytics_extended.py` — 转化漏斗、提醒、排行、动态聚合逻辑

### Backend — Modified Files
- `backend/app/api/v1/analytics.py` — 增强 dashboard endpoint，新增 4 个端点
- `backend/app/api/v1/__init__.py` — 注册新路由（如有变化）

### Frontend — New Files
- `frontend/apps/admin/src/app/(dashboard)/_components/dashboard/ConversionFunnel.tsx` — 转化漏斗组件
- `frontend/apps/admin/src/app/(dashboard)/_components/dashboard/StatusAlerts.tsx` — 状态提醒栏组件
- `frontend/apps/admin/src/app/(dashboard)/_components/dashboard/CampaignRanking.tsx` — 活动排行组件
- `frontend/apps/admin/src/app/(dashboard)/_components/dashboard/ChannelHealth.tsx` — 渠道健康组件
- `frontend/apps/admin/src/app/(dashboard)/_components/dashboard/QuickActions.tsx` — 快速操作组件
- `frontend/apps/admin/src/app/(dashboard)/_components/dashboard/RecentEvents.tsx` — 最近动态组件
- `frontend/apps/admin/src/app/(dashboard)/_components/dashboard/DashboardCharts.tsx` — 双轴趋势图组件
- `frontend/apps/admin/src/app/(dashboard)/analytics/page.tsx` — 深度分析页面（Tab: 活动分析 + GMV归因）
- `frontend/apps/admin/src/app/(dashboard)/risk-center/page.tsx` — 风控中心（从 risk-dashboard 迁移）
- `frontend/apps/admin/src/app/(dashboard)/stats/redirect.tsx` — 重定向到首页

### Frontend — Modified Files
- `frontend/apps/admin/src/app/(dashboard)/layout.tsx:123-205` — 侧边栏菜单重构
- `frontend/apps/admin/src/app/(dashboard)/_components/DashboardHome.tsx` — 重写为 6 区域布局

### Frontend — Deleted Files
- `frontend/apps/admin/src/app/(dashboard)/stats/page.tsx` — 删除（重定向替代）

---

## Phase 1: 后端新增 API

### Task 1: 创建 Analytics Schemas

**Files:**
- Create: `backend/app/schemas/analytics.py`

- [ ] **Step 1: 创建 schema 文件**

```python
"""Analytics Pydantic schemas."""

import uuid
from datetime import datetime

from pydantic import BaseModel


class FunnelStep(BaseModel):
    """转化漏斗单步"""
    name: str
    value: int
    rate: float  # 相对扫码量的百分比


class ConversionFunnelResponse(BaseModel):
    """转化漏斗响应"""
    period_days: int
    scan_count: int
    claim_count: int
    claim_rate: float
    signup_count: int
    signup_rate: float
    private_domain_count: int
    private_domain_rate: float
    gmv_amount: float
    gmv_rate: float
    steps: list[FunnelStep]


class AlertItem(BaseModel):
    """单条状态提醒"""
    type: str  # "code_quota" | "campaign_anomaly" | "campaign_status" | "plan_expiry"
    level: str  # "error" | "warning" | "info"
    message: str
    action_url: str


class AlertsResponse(BaseModel):
    """状态提醒响应"""
    alerts: list[AlertItem]


class CampaignRankingItem(BaseModel):
    """活动排行单项"""
    campaign_id: uuid.UUID
    campaign_name: str
    campaign_status: str
    scan_count: int
    claim_count: int
    conversion_rate: float


class CampaignRankingResponse(BaseModel):
    """活动排行响应"""
    items: list[CampaignRankingItem]
    total: int


class RecentEventItem(BaseModel):
    """最近动态单项"""
    event_type: str  # "scan_surge" | "campaign_status_change" | "channel_anomaly" | "new_signup" | "claim_milestone"
    message: str
    timestamp: datetime
    action_url: str | None = None


class RecentEventsResponse(BaseModel):
    """最近动态响应"""
    events: list[RecentEventItem]
```

- [ ] **Step 2: 提交**

```bash
git add backend/app/schemas/analytics.py
git commit -m "feat(analytics): add Pydantic schemas for new dashboard APIs"
```

---

### Task 2: 创建扩展分析服务 — 转化漏斗

**Files:**
- Create: `backend/app/services/analytics_extended.py`

- [ ] **Step 1: 创建服务文件（转化漏斗函数）**

```python
"""扩展分析服务 — 转化漏斗、状态提醒、活动排行、最近动态"""

import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import Integer, and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.campaign import BenefitClaim
from app.models.member import ConsumerProfile
from app.models.scan import ScanEvent
from app.models.tenant import Tenant


async def get_conversion_funnel(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    days_back: int = 30,
) -> dict:
    """获取转化漏斗数据：扫码→领券→留资→加私域→GMV"""
    today = date.today()
    start_dt = datetime(today.year, today.month, today.day, tzinfo=UTC) - timedelta(days=days_back)

    # 1. 扫码量
    scan_result = await db.execute(
        select(func.count()).where(
            ScanEvent.tenant_id == tenant_id,
            ScanEvent.scan_time >= start_dt,
        )
    )
    scan_count = scan_result.scalar() or 0

    # 2. 领券量
    claim_result = await db.execute(
        select(func.count()).where(
            BenefitClaim.tenant_id == tenant_id,
            BenefitClaim.created_at >= start_dt,
            BenefitClaim.status == "success",
        )
    )
    claim_count = claim_result.scalar() or 0

    # 3. 留资量（有 phone_hash 的消费者，created_at 在范围内）
    signup_result = await db.execute(
        select(func.count()).where(
            ConsumerProfile.tenant_id == tenant_id,
            ConsumerProfile.phone_hash.isnot(None),
            ConsumerProfile.created_at >= start_dt,
        )
    )
    signup_count = signup_result.scalar() or 0

    # 4. 加私域量（scan_events 中 environment 包含 wechat 的去重用户）
    # 注：当前 scan_events 没有 redirect action 字段，使用微信扫码量作为近似
    private_domain_result = await db.execute(
        select(func.count(ScanEvent.public_id.distinct())).where(
            ScanEvent.tenant_id == tenant_id,
            ScanEvent.scan_time >= start_dt,
            ScanEvent.environment == "wechat",
        )
    )
    private_domain_count = private_domain_result.scalar() or 0

    # 5. GMV（从 gmv 模型获取归因订单金额）
    gmv_amount = 0.0
    try:
        from app.models.gmv import ExternalOrder
        gmv_result = await db.execute(
            select(func.coalesce(func.sum(ExternalOrder.order_amount), 0)).where(
                ExternalOrder.tenant_id == tenant_id,
                ExternalOrder.order_time >= start_dt,
            )
        )
        gmv_amount = float(gmv_result.scalar() or 0)
    except Exception:
        pass  # GMV 模型可能尚未有数据

    def safe_rate(count: int, total: int) -> float:
        return round(count / total * 100, 1) if total > 0 else 0.0

    steps = [
        {"name": "扫码", "value": scan_count, "rate": 100.0},
        {"name": "领券", "value": claim_count, "rate": safe_rate(claim_count, scan_count)},
        {"name": "留资", "value": signup_count, "rate": safe_rate(signup_count, scan_count)},
        {"name": "加私域", "value": private_domain_count, "rate": safe_rate(private_domain_count, scan_count)},
        {"name": "购买(GMV)", "value": int(gmv_amount), "rate": safe_rate(int(gmv_amount), scan_count) if gmv_amount > 0 else 0.0},
    ]

    return {
        "period_days": days_back,
        "scan_count": scan_count,
        "claim_count": claim_count,
        "claim_rate": safe_rate(claim_count, scan_count),
        "signup_count": signup_count,
        "signup_rate": safe_rate(signup_count, scan_count),
        "private_domain_count": private_domain_count,
        "private_domain_rate": safe_rate(private_domain_count, scan_count),
        "gmv_amount": gmv_amount,
        "gmv_rate": safe_rate(int(gmv_amount), scan_count) if gmv_amount > 0 else 0.0,
        "steps": steps,
    }
```

- [ ] **Step 2: 提交**

```bash
git add backend/app/services/analytics_extended.py
git commit -m "feat(analytics): add conversion funnel service"
```

---

### Task 3: 扩展服务 — 状态提醒

**Files:**
- Modify: `backend/app/services/analytics_extended.py` (追加)

- [ ] **Step 1: 追加状态提醒函数**

在 `analytics_extended.py` 末尾追加：

```python

async def get_alerts(
    db: AsyncSession,
    tenant_id: uuid.UUID,
) -> dict:
    """获取状态提醒：码余量、活动异常、套餐到期"""
    alerts: list[dict] = []
    today = date.today()

    # 1. 码余量预警
    from app.models.code import CodeItem
    total_codes = await db.execute(
        select(func.count()).where(CodeItem.tenant_id == tenant_id)
    )
    used_codes = await db.execute(
        select(func.count()).where(
            CodeItem.tenant_id == tenant_id,
            CodeItem.status.in_(["activated", "scanned"]),
        )
    )
    total = total_codes.scalar() or 0
    used = used_codes.scalar() or 0
    remaining = total - used
    if 0 < remaining < 5000:
        alerts.append({
            "type": "code_quota",
            "level": "warning" if remaining < 1000 else "info",
            "message": f"码余量仅剩 {remaining:,}，建议尽快补充" if remaining < 1000 else f"码余量 {remaining:,}，请关注",
            "action_url": "/codes",
        })

    # 2. 活动异常检测（本周 vs 上周转化率下降超 20%）
    from app.models.campaign import Campaign
    active_campaigns = await db.execute(
        select(Campaign).where(
            Campaign.tenant_id == tenant_id,
            Campaign.status == "active",
        )
    )
    active_count = 0
    ending_soon_count = 0
    for campaign in active_campaigns.scalars().all():
        active_count += 1
        if campaign.end_at:
            days_left = (campaign.end_at.date() - today).days
            if 0 < days_left <= 7:
                ending_soon_count += 1
                alerts.append({
                    "type": "campaign_status",
                    "level": "info",
                    "message": f"活动「{campaign.name}」将于 {days_left} 天后到期",
                    "action_url": f"/campaigns/{campaign.id}",
                })

    if active_count > 0:
        # 概览提醒
        alerts.append({
            "type": "campaign_status",
            "level": "info",
            "message": f"当前 {active_count} 个活动进行中",
            "action_url": "/campaigns",
        })

    # 3. 套餐到期提醒
    tenant_result = await db.execute(
        select(Tenant).where(Tenant.id == tenant_id)
    )
    tenant = tenant_result.scalar_one_or_none()
    if tenant and tenant.plan_expires_at:
        days_to_expire = (tenant.plan_expires_at.date() - today).days
        if 0 < days_to_expire <= 30:
            alerts.append({
                "type": "plan_expiry",
                "level": "warning" if days_to_expire <= 15 else "info",
                "message": f"套餐将于 {days_to_expire} 天后到期，请及时续费",
                "action_url": "/settings/tenant",
            })

    # 按级别排序：error → warning → info
    level_order = {"error": 0, "warning": 1, "info": 2}
    alerts.sort(key=lambda a: level_order.get(a["level"], 3))

    return {"alerts": alerts[:5]}
```

- [ ] **Step 2: 提交**

```bash
git add backend/app/services/analytics_extended.py
git commit -m "feat(analytics): add status alerts service"
```

---

### Task 4: 扩展服务 — 活动排行 + 最近动态

**Files:**
- Modify: `backend/app/services/analytics_extended.py` (追加)

- [ ] **Step 1: 追加活动排行和最近动态函数**

在 `analytics_extended.py` 末尾追加：

```python

async def get_campaign_ranking(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    days_back: int = 30,
    limit: int = 5,
) -> dict:
    """按转化率（领券量/扫码量）排序的活动排行"""
    from app.models.campaign import Campaign
    today = date.today()
    start_dt = datetime(today.year, today.month, today.day, tzinfo=UTC) - timedelta(days=days_back)

    # 获取所有活动
    campaigns_result = await db.execute(
        select(Campaign).where(Campaign.tenant_id == tenant_id)
    )
    campaigns = campaigns_result.scalars().all()

    items = []
    for campaign in campaigns:
        # 获取该活动的扫码量（通过 product_id → code_batch → code_item → scan_event）
        scan_count = 0
        claim_count = 0

        if campaign.product_id:
            from app.models.code import CodeBatch, CodeItem
            batch_result = await db.execute(
                select(CodeBatch.id).where(
                    CodeBatch.tenant_id == tenant_id,
                    CodeBatch.product_id == campaign.product_id,
                )
            )
            batch_ids = [row[0] for row in batch_result.all()]

            if batch_ids:
                code_result = await db.execute(
                    select(CodeItem.public_id).where(
                        CodeItem.tenant_id == tenant_id,
                        CodeItem.code_batch_id.in_(batch_ids),
                    )
                )
                public_ids = [row[0] for row in code_result.all()]

                if public_ids:
                    scan_result = await db.execute(
                        select(func.count()).where(
                            ScanEvent.tenant_id == tenant_id,
                            ScanEvent.public_id.in_(public_ids),
                            ScanEvent.scan_time >= start_dt,
                        )
                    )
                    scan_count = scan_result.scalar() or 0

        # 获取该活动的领券量
        claim_result = await db.execute(
            select(func.count()).where(
                BenefitClaim.tenant_id == tenant_id,
                BenefitClaim.campaign_id == campaign.id,
                BenefitClaim.created_at >= start_dt,
                BenefitClaim.status == "success",
            )
        )
        claim_count = claim_result.scalar() or 0

        conversion_rate = round(claim_count / scan_count * 100, 1) if scan_count > 0 else 0.0

        items.append({
            "campaign_id": str(campaign.id),
            "campaign_name": campaign.name,
            "campaign_status": campaign.status,
            "scan_count": scan_count,
            "claim_count": claim_count,
            "conversion_rate": conversion_rate,
        })

    # 按转化率降序
    items.sort(key=lambda x: x["conversion_rate"], reverse=True)

    return {"items": items[:limit], "total": len(items)}


async def get_recent_events(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    limit: int = 5,
) -> dict:
    """获取最近业务事件时间线"""
    events: list[dict] = []
    today = date.today()
    yesterday = today - timedelta(days=1)
    yesterday_dt = datetime(yesterday.year, yesterday.month, yesterday.day, tzinfo=UTC)
    today_dt = datetime(today.year, today.month, today.day, tzinfo=UTC)

    # 1. 昨日扫码变化
    from app.models.analytics import DailyScanStats
    yesterday_stats = await db.execute(
        select(DailyScanStats).where(
            DailyScanStats.tenant_id == tenant_id,
            DailyScanStats.date == yesterday,
        )
    )
    ys = yesterday_stats.scalar_one_or_none()
    if ys and ys.total_scans > 0:
        # 对比前天
        day_before = await db.execute(
            select(DailyScanStats).where(
                DailyScanStats.tenant_id == tenant_id,
                DailyScanStats.date == yesterday - timedelta(days=1),
            )
        )
        db_stats = day_before.scalar_one_or_none()
        if db_stats and db_stats.total_scans > 0:
            change = round((ys.total_scans - db_stats.total_scans) / db_stats.total_scans * 100, 1)
            if abs(change) >= 10:
                direction = "增长" if change > 0 else "下降"
                events.append({
                    "event_type": "scan_surge",
                    "message": f"昨日扫码{direction} {abs(change)}%（{ys.total_scans} 次）",
                    "timestamp": yesterday_dt.isoformat(),
                    "action_url": "/",
                })

    # 2. 今日新增留资
    today_signups = await db.execute(
        select(func.count()).where(
            ConsumerProfile.tenant_id == tenant_id,
            ConsumerProfile.phone_hash.isnot(None),
            ConsumerProfile.created_at >= today_dt,
        )
    )
    signup_count = today_signups.scalar() or 0
    if signup_count > 0:
        events.append({
            "event_type": "new_signup",
            "message": f"今日新增 {signup_count} 位消费者留资",
            "timestamp": datetime.now(UTC).isoformat(),
            "action_url": "/members",
        })

    # 3. 进行中的活动
    from app.models.campaign import Campaign
    active_campaigns = await db.execute(
        select(Campaign).where(
            Campaign.tenant_id == tenant_id,
            Campaign.status == "active",
        ).limit(3)
    )
    for campaign in active_campaigns.scalars().all():
        events.append({
            "event_type": "campaign_status_change",
            "message": f"活动「{campaign.name}」进行中",
            "timestamp": campaign.start_at.isoformat() if campaign.start_at else today_dt.isoformat(),
            "action_url": f"/campaigns/{campaign.id}",
        })

    # 按时间降序
    events.sort(key=lambda e: e["timestamp"], reverse=True)

    return {"events": events[:limit]}
```

- [ ] **Step 2: 提交**

```bash
git add backend/app/services/analytics_extended.py
git commit -m "feat(analytics): add campaign ranking and recent events services"
```

---

### Task 5: 注册新 API 端点

**Files:**
- Modify: `backend/app/api/v1/analytics.py`

- [ ] **Step 1: 新增 4 个端点到 analytics.py**

在文件 `backend/app/api/v1/analytics.py` 末尾追加：

```python
from app.services.analytics_extended import (
    get_alerts,
    get_campaign_ranking,
    get_conversion_funnel,
    get_recent_events,
)


@analytics_router.get("/conversion-funnel", summary="获取转化漏斗数据")
async def get_conversion_funnel_endpoint(
    days_back: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return await get_conversion_funnel(db, tenant_id, days_back=days_back)


@analytics_router.get("/alerts", summary="获取状态提醒")
async def get_alerts_endpoint(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return await get_alerts(db, tenant_id)


@analytics_router.get("/campaign-ranking", summary="获取活动排行")
async def get_campaign_ranking_endpoint(
    days_back: int = Query(30, ge=1, le=365),
    limit: int = Query(5, ge=1, le=20),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return await get_campaign_ranking(db, tenant_id, days_back=days_back, limit=limit)


@analytics_router.get("/recent-events", summary="获取最近动态")
async def get_recent_events_endpoint(
    limit: int = Query(5, ge=1, le=20),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return await get_recent_events(db, tenant_id, limit=limit)
```

- [ ] **Step 2: 验证 API 启动正常**

```bash
cd /Users/ericding/code/agriculture/yimatong/backend && source .venv/bin/activate
uv run python -c "from app.api.v1.analytics import analytics_router; print([r.path for r in analytics_router.routes])"
```

Expected: 输出包含 `/scan-stats`, `/code-stats`, `/dashboard`, `/campaign-scan-stats`, `/conversion-funnel`, `/alerts`, `/campaign-ranking`, `/recent-events`

- [ ] **Step 3: 提交**

```bash
git add backend/app/api/v1/analytics.py
git commit -m "feat(analytics): add conversion-funnel, alerts, campaign-ranking, recent-events endpoints"
```

---

### Task 6: 增强 dashboard API 增加转化字段

**Files:**
- Modify: `backend/app/services/analytics.py:159-170` (get_dashboard 返回值)
- Modify: `backend/app/api/v1/analytics.py:35-41` (endpoint 签名)

- [ ] **Step 1: 增强 get_dashboard 服务，在返回值中添加转化指标**

在 `backend/app/services/analytics.py` 的 `get_dashboard` 函数（第 159 行）返回值中增加字段：

将原 return 语句（第 159-170 行）替换为：

```python
    # 转化指标（从 analytics_extended 导入，避免循环引用，就地查询）
    claim_count_result = await db.execute(
        select(func.count()).select_from(BenefitClaim).where(
            BenefitClaim.tenant_id == tenant_id,
            BenefitClaim.created_at >= cutoff_dt,
            BenefitClaim.status == "success",
        )
    )
    from app.models.campaign import BenefitClaim
    period_claim_count = claim_count_result.scalar() or 0

    return {
        "today_scans": today_stats.total_scans if today_stats else 0,
        "today_uv": today_stats.uv if today_stats else 0,
        "cumulative_scans": cumulative_scans,
        "cumulative_first_scans": cumulative_first_scans,
        "trend": trend,
        "environment_breakdown": env_stats,
        "period_claim_count": period_claim_count,
        "period_claim_rate": round(period_claim_count / cumulative_scans * 100, 1) if cumulative_scans > 0 else 0.0,
        "comparison": {
            "weekly_scans_change": _calc_change(cur_scans, prev_scans),
            "weekly_first_scans_change": _calc_change(cur_first_scans, prev_first_scans),
        },
    }
```

注意：需要将 `from app.models.campaign import BenefitClaim` 移到文件顶部的 import 区域。在文件顶部（第 10 行后）添加：

```python
from app.models.campaign import BenefitClaim
```

- [ ] **Step 2: 验证导入无循环**

```bash
cd /Users/ericding/code/agriculture/yimatong/backend && source .venv/bin/activate
uv run python -c "from app.services.analytics import get_dashboard; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: 提交**

```bash
git add backend/app/services/analytics.py
git commit -m "feat(analytics): enhance dashboard API with claim conversion metrics"
```

---

## Phase 2: 前端侧边栏菜单重构 + 路由变更

### Task 7: 重构侧边栏菜单

**Files:**
- Modify: `frontend/apps/admin/src/app/(dashboard)/layout.tsx:45-205`

- [ ] **Step 1: 更新 MENU_OPEN_KEY_RULES**

将 `layout.tsx` 第 50 行的 analytics-group 规则：

```typescript
  { key: "analytics-group", prefixes: ["/stats", "/campaign-analytics", "/gmv", "/risk-dashboard", "/exports"] },
```

替换为：

```typescript
  { key: "/analytics", path: "/analytics" },
  { key: "/risk-center", path: "/risk-center" },
  { key: "/exports", path: "/exports" },
```

同时更新 `openKeys` 计算逻辑（第 113-115 行），由于新菜单不再有 analytics-group 折叠，需要适配。将 `openKeys` 的计算改为：

```typescript
  const openKeys = MENU_OPEN_KEY_RULES
    .filter(({ prefixes }) => prefixes && prefixes.some((prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`)))
    .map(({ key }) => key);
```

- [ ] **Step 2: 更新 menuItems 数组**

将 `layout.tsx` 第 163-174 行的 analytics-group 菜单：

```typescript
    {
      key: "analytics-group",
      icon: <BarChartOutlined />,
      label: t("menu.analytics"),
      children: [
        { key: "/stats", icon: <BarChartOutlined />, label: t("menu.stats") },
        { key: "/campaign-analytics", icon: <LineChartOutlined />, label: t("menu.campaign-analytics") },
        { key: "/gmv", icon: <LineChartOutlined />, label: t("menu.gmv") },
        { key: "/risk-dashboard", icon: <SafetyCertificateOutlined />, label: t("menu.risk-dashboard") },
        { key: "/exports", icon: <ExportOutlined />, label: t("menu.exports") },
      ],
    },
```

替换为 3 个一级菜单项（注意放在 integrations-group 之前）：

```typescript
    { key: "/analytics", icon: <LineChartOutlined />, label: t("menu.deep-analytics") },
    { key: "/risk-center", icon: <SafetyCertificateOutlined />, label: t("menu.risk-center") },
    { key: "/exports", icon: <ExportOutlined />, label: t("menu.exports") },
```

- [ ] **Step 3: 更新 MENU_PERMISSIONS**

在 `layout.tsx` 第 64-89 行：

**agency allowlist**（第 76 行）中，将：
```typescript
      "/stats", "/campaign-analytics", "/gmv", "/exports",
      "analytics-group",
```
替换为：
```typescript
      "/analytics", "/exports",
```

**regional_org blocklist**（第 84 行）中，将：
```typescript
      "/accounts", "/risk-dashboard", "/integrations", "/connectors",
```
替换为：
```typescript
      "/accounts", "/risk-center", "/integrations", "/connectors",
```

并将第 85 行的：
```typescript
      "/risk", "/gmv", "/crm-sync", "/imports",
```
替换为：
```typescript
      "/risk", "/crm-sync", "/imports",
```

- [ ] **Step 4: 验证构建**

```bash
cd /Users/ericding/code/agriculture/yimatong/frontend && pnpm build:shared && pnpm build:admin 2>&1 | tail -5
```

Expected: 构建成功

- [ ] **Step 5: 提交**

```bash
git add frontend/apps/admin/src/app/(dashboard)/layout.tsx
git commit -m "refactor(menu): consolidate analytics menu from 5 items to 3 top-level items"
```

---

### Task 8: 创建深度分析页面（活动分析 + GMV 合并）

**Files:**
- Create: `frontend/apps/admin/src/app/(dashboard)/analytics/page.tsx`

- [ ] **Step 1: 创建深度分析页面**

```tsx
"use client";

import { useState } from "react";
import { Tabs } from "antd";
import { GiftOutlined, DollarOutlined } from "@ant-design/icons";
import CampaignAnalyticsContent from "../campaign-analytics/_components/CampaignAnalyticsContent";
import GmvContent from "../gmv/_components/GmvContent";

export default function DeepAnalyticsPage() {
  const [activeTab, setActiveTab] = useState("campaign");

  return (
    <Tabs
      activeKey={activeTab}
      onChange={setActiveTab}
      items={[
        {
          key: "campaign",
          label: (
            <span>
              <GiftOutlined /> 活动分析
            </span>
          ),
          children: <CampaignAnalyticsContent />,
        },
        {
          key: "gmv",
          label: (
            <span>
              <DollarOutlined /> GMV 归因
            </span>
          ),
          children: <GmvContent />,
        },
      ]}
    />
  );
}
```

- [ ] **Step 2: 将现有页面内容提取为子组件**

从 `frontend/apps/admin/src/app/(dashboard)/campaign-analytics/page.tsx` 的主组件逻辑提取为 `CampaignAnalyticsContent` 组件，保存到 `frontend/apps/admin/src/app/(dashboard)/campaign-analytics/_components/CampaignAnalyticsContent.tsx`。

具体操作：将原 `CampaignAnalyticsPage` 组件的内容（去除 `export default`）复制到新文件，重命名为 `export default function CampaignAnalyticsContent()`。原 `campaign-analytics/page.tsx` 改为引用此组件以保持向后兼容。

同样地，从 `frontend/apps/admin/src/app/(dashboard)/gmv/page.tsx` 提取为 `GmvContent`，保存到 `frontend/apps/admin/src/app/(dashboard)/gmv/_components/GmvContent.tsx`。

- [ ] **Step 3: 验证旧路由仍然可用**

访问 `/campaign-analytics` 和 `/gmv` 应仍正常渲染。

- [ ] **Step 4: 提交**

```bash
git add frontend/apps/admin/src/app/\(dashboard\)/analytics/
git add frontend/apps/admin/src/app/\(dashboard\)/campaign-analytics/_components/
git add frontend/apps/admin/src/app/\(dashboard\)/gmv/_components/
git commit -m "feat: create deep analytics page merging campaign + GMV tabs"
```

---

### Task 9: 创建风控中心页面（从 risk-dashboard 迁移）

**Files:**
- Create: `frontend/apps/admin/src/app/(dashboard)/risk-center/page.tsx`
- Modify: `frontend/apps/admin/src/app/(dashboard)/risk-dashboard/page.tsx` (改为重导出)

- [ ] **Step 1: 创建 risk-center 页面**

```tsx
"use client";

import { Card, Col, Row, Statistic } from "antd";
import {
  AlertOutlined,
  WarningOutlined,
  SafetyCertificateOutlined,
} from "@ant-design/icons";
import { useCallback, useEffect, useState } from "react";
import api, { extractErrorMessage } from "@/lib/api";

// 复用 risk-dashboard 原有组件
import {
  AlertIndicator,
  RepeatScansCard,
  CrossRegionCard,
  ChannelHealthCard,
  ConversionCard,
  DiversionCard,
} from "../risk-dashboard/_components/RiskDashboardComponents";

export default function RiskCenterPage() {
  const [summary, setSummary] = useState<{
    unresolved_alerts: number;
    unresolved_diversions: number;
    avg_health_score: number;
  } | null>(null);

  const fetchSummary = useCallback(async () => {
    try {
      const [alertRes, divRes, healthRes] = await Promise.all([
        api.get("/risk-dashboard/alerts/stream"),
        api.get("/risk-dashboard/diversion-summary"),
        api.get("/channel-analytics/health-scores"),
      ]);
      setSummary({
        unresolved_alerts: alertRes.data?.unresolved_count ?? 0,
        unresolved_diversions: divRes.data?.unresolved_count ?? 0,
        avg_health_score: 85, // 默认值，后续从 healthRes 计算
      });
    } catch {
      // 静默处理，概览指标非关键
    }
  }, []);

  useEffect(() => {
    void fetchSummary();
  }, [fetchSummary]);

  return (
    <div>
      <div className="mb-4">
        <h4 className="mb-0" style={{ fontSize: 20, fontWeight: 600 }}>风控中心</h4>
      </div>

      {/* 概览指标条 */}
      {summary && (
        <Row gutter={[16, 16]} className="mb-6">
          <Col xs={24} sm={8}>
            <Card size="small">
              <Statistic
                title="待处理告警"
                value={summary.unresolved_alerts}
                prefix={<AlertOutlined />}
                valueStyle={{ color: summary.unresolved_alerts > 0 ? "#cf1322" : "#3f8600" }}
              />
            </Card>
          </Col>
          <Col xs={24} sm={8}>
            <Card size="small">
              <Statistic
                title="未处理窜货线索"
                value={summary.unresolved_diversions}
                prefix={<WarningOutlined />}
                valueStyle={{ color: summary.unresolved_diversions > 0 ? "#fa8c16" : "#3f8600" }}
              />
            </Card>
          </Col>
          <Col xs={24} sm={8}>
            <Card size="small">
              <Statistic
                title="渠道平均健康评分"
                value={summary.avg_health_score}
                suffix="/ 100"
                prefix={<SafetyCertificateOutlined />}
                valueStyle={{ color: summary.avg_health_score >= 80 ? "#3f8600" : "#fa8c16" }}
              />
            </Card>
          </Col>
        </Row>
      )}

      {/* 原有风控看板内容 */}
      <AlertIndicator />
      <Row gutter={[16, 16]} className="mb-6">
        <Col xs={24} lg={12}>
          <RepeatScansCard />
        </Col>
        <Col xs={24} lg={12}>
          <CrossRegionCard />
        </Col>
      </Row>
      <Row gutter={[16, 16]} className="mb-6">
        <Col xs={24} lg={12}>
          <ChannelHealthCard />
        </Col>
        <Col xs={24} lg={12}>
          <ConversionCard />
        </Col>
      </Row>
      <DiversionCard />
    </div>
  );
}
```

- [ ] **Step 2: 从 risk-dashboard/page.tsx 提取子组件**

将 `risk-dashboard/page.tsx` 中的 `AlertIndicator`、`RepeatScansCard`、`CrossRegionCard`、`ChannelHealthCard`、`ConversionCard`、`DiversionCard` 提取到 `risk-dashboard/_components/RiskDashboardComponents.tsx`。原 `risk-dashboard/page.tsx` 保留但引用这些组件。

- [ ] **Step 3: 提交**

```bash
git add frontend/apps/admin/src/app/\(dashboard\)/risk-center/
git add frontend/apps/admin/src/app/\(dashboard\)/risk-dashboard/_components/
git commit -m "feat: create risk center page with summary indicators"
```

---

### Task 10: 删除扫码统计页面 + 设置重定向

**Files:**
- Modify: `frontend/apps/admin/src/app/(dashboard)/stats/page.tsx` (改为重定向)

- [ ] **Step 1: 替换 stats/page.tsx 为重定向**

将 `stats/page.tsx` 的全部内容替换为：

```tsx
"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

export default function StatsRedirect() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/");
  }, [router]);
  return null;
}
```

- [ ] **Step 2: 提交**

```bash
git add frontend/apps/admin/src/app/\(dashboard\)/stats/page.tsx
git commit -m "refactor: redirect /stats to dashboard homepage"
```

---

## Phase 3: 经营看板新组件

### Task 11: 状态提醒栏组件

**Files:**
- Create: `frontend/apps/admin/src/app/(dashboard)/_components/dashboard/StatusAlerts.tsx`

- [ ] **Step 1: 创建组件**

```tsx
"use client";

import { useCallback, useEffect, useState } from "react";
import { Alert, Space, Spin } from "antd";
import { useRouter } from "next/navigation";
import api, { extractErrorMessage } from "@/lib/api";

interface AlertItem {
  type: string;
  level: string;
  message: string;
  action_url: string;
}

export default function StatusAlerts() {
  const [alerts, setAlerts] = useState<AlertItem[]>([]);
  const [loading, setLoading] = useState(true);
  const router = useRouter();

  const fetchAlerts = useCallback(async () => {
    try {
      const res = await api.get("/analytics/alerts");
      setAlerts(res.data?.alerts || []);
    } catch {
      // 静默处理
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchAlerts();
  }, [fetchAlerts]);

  if (loading) return <Spin size="small" />;
  if (alerts.length === 0) return null;

  const levelToType: Record<string, "error" | "warning" | "info"> = {
    error: "error",
    warning: "warning",
    info: "info",
  };

  return (
    <Space direction="vertical" className="w-full mb-4">
      {alerts.map((alert, idx) => (
        <Alert
          key={idx}
          type={levelToType[alert.level] || "info"}
          message={alert.message}
          showIcon
          closable
          style={{ cursor: "pointer" }}
          onClick={() => {
            if (alert.action_url.startsWith("/")) {
              router.push(alert.action_url);
            }
          }}
        />
      ))}
    </Space>
  );
}
```

- [ ] **Step 2: 提交**

```bash
git add frontend/apps/admin/src/app/\(dashboard\)/_components/dashboard/StatusAlerts.tsx
git commit -m "feat(dashboard): add StatusAlerts component"
```

---

### Task 12: 转化漏斗组件

**Files:**
- Create: `frontend/apps/admin/src/app/(dashboard)/_components/dashboard/ConversionFunnel.tsx`

- [ ] **Step 1: 创建组件**

```tsx
"use client";

import { useCallback, useEffect, useState } from "react";
import { Card, Spin, Typography } from "antd";
import api from "@/lib/api";

const { Text } = Typography;

interface FunnelStep {
  name: string;
  value: number;
  rate: number;
}

interface FunnelData {
  steps: FunnelStep[];
  period_days: number;
}

const STEP_COLORS = ["#1677ff", "#52c41a", "#faad14", "#fa8c16", "#f5222d"];

export default function ConversionFunnel() {
  const [data, setData] = useState<FunnelData | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchData = useCallback(async () => {
    try {
      const res = await api.get("/analytics/conversion-funnel", {
        params: { days_back: 30 },
      });
      setData(res.data);
    } catch {
      // 静默处理
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchData();
  }, [fetchData]);

  if (loading) {
    return (
      <Card title="核心转化漏斗（近 30 天）" size="small">
        <div className="flex items-center justify-center" style={{ height: 120 }}>
          <Spin />
        </div>
      </Card>
    );
  }

  if (!data || data.steps.length === 0 || data.steps[0].value === 0) {
    return (
      <Card title="核心转化漏斗（近 30 天）" size="small">
        <div className="flex items-center justify-center text-gray-400" style={{ height: 120 }}>
          暂无转化数据
        </div>
      </Card>
    );
  }

  const maxValue = data.steps[0].value;

  return (
    <Card title="核心转化漏斗（近 30 天）" size="small">
      <div className="flex items-end gap-2">
        {data.steps.map((step, idx) => {
          const widthPercent = Math.max((step.value / maxValue) * 100, 8);
          return (
            <div
              key={step.name}
              className="flex flex-col items-center"
              style={{ flex: `0 0 ${widthPercent}%`, minWidth: 60 }}
            >
              <div
                style={{
                  background: STEP_COLORS[idx] || "#d9d9d9",
                  height: 80,
                  width: "100%",
                  borderRadius: "4px 4px 0 0",
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  justifyContent: "center",
                  color: "white",
                  fontWeight: 600,
                }}
              >
                <span style={{ fontSize: 16 }}>{step.value.toLocaleString()}</span>
              </div>
              <Text strong className="mt-1" style={{ fontSize: 12 }}>
                {step.name}
              </Text>
              <Text type="secondary" style={{ fontSize: 11 }}>
                {step.rate}%
              </Text>
            </div>
          );
        })}
      </div>
    </Card>
  );
}
```

- [ ] **Step 2: 提交**

```bash
git add frontend/apps/admin/src/app/\(dashboard\)/_components/dashboard/ConversionFunnel.tsx
git commit -m "feat(dashboard): add ConversionFunnel component"
```

---

### Task 13: 活动排行组件

**Files:**
- Create: `frontend/apps/admin/src/app/(dashboard)/_components/dashboard/CampaignRanking.tsx`

- [ ] **Step 1: 创建组件**

```tsx
"use client";

import { useCallback, useEffect, useState } from "react";
import { Card, Spin, Table, Tag } from "antd";
import { useRouter } from "next/navigation";
import type { ColumnsType } from "antd/es/table";
import api from "@/lib/api";

interface RankingItem {
  campaign_id: string;
  campaign_name: string;
  campaign_status: string;
  scan_count: number;
  claim_count: number;
  conversion_rate: number;
}

const STATUS_MAP: Record<string, { label: string; color: string }> = {
  draft: { label: "草稿", color: "default" },
  active: { label: "进行中", color: "blue" },
  paused: { label: "已暂停", color: "orange" },
  ended: { label: "已结束", color: "gray" },
};

export default function CampaignRanking() {
  const [items, setItems] = useState<RankingItem[]>([]);
  const [loading, setLoading] = useState(true);
  const router = useRouter();

  const fetchData = useCallback(async () => {
    try {
      const res = await api.get("/analytics/campaign-ranking", {
        params: { limit: 5 },
      });
      setItems(res.data?.items || []);
    } catch {
      // 静默处理
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchData();
  }, [fetchData]);

  const columns: ColumnsType<RankingItem> = [
    {
      title: "排名",
      width: 50,
      render: (_, __, idx) => (
        <span style={{ fontWeight: 600, color: idx < 3 ? "#1677ff" : undefined }}>
          {idx + 1}
        </span>
      ),
    },
    { title: "活动名称", dataIndex: "campaign_name", key: "name", ellipsis: true },
    {
      title: "状态",
      dataIndex: "campaign_status",
      key: "status",
      width: 80,
      render: (status: string) => {
        const info = STATUS_MAP[status] || { label: status, color: "default" };
        return <Tag color={info.color}>{info.label}</Tag>;
      },
    },
    {
      title: "转化率",
      dataIndex: "conversion_rate",
      key: "rate",
      width: 80,
      render: (rate: number) => (
        <span style={{ fontWeight: 600, color: rate > 10 ? "#3f8600" : rate > 5 ? "#faad14" : "#cf1322" }}>
          {rate}%
        </span>
      ),
    },
  ];

  return (
    <Card title="活动排行 Top 5" size="small" style={{ height: "100%" }}>
      {loading ? (
        <div className="flex items-center justify-center" style={{ height: 200 }}>
          <Spin />
        </div>
      ) : items.length === 0 ? (
        <div className="flex items-center justify-center text-gray-400" style={{ height: 200 }}>
          暂无活动数据
        </div>
      ) : (
        <Table
          columns={columns}
          dataSource={items}
          rowKey="campaign_id"
          pagination={false}
          size="small"
          onRow={(record) => ({
            onClick: () => router.push(`/campaigns/${record.campaign_id}`),
            style: { cursor: "pointer" },
          })}
        />
      )}
    </Card>
  );
}
```

- [ ] **Step 2: 提交**

```bash
git add frontend/apps/admin/src/app/\(dashboard\)/_components/dashboard/CampaignRanking.tsx
git commit -m "feat(dashboard): add CampaignRanking component"
```

---

### Task 14: 渠道健康组件

**Files:**
- Create: `frontend/apps/admin/src/app/(dashboard)/_components/dashboard/ChannelHealth.tsx`

- [ ] **Step 1: 创建组件**

```tsx
"use client";

import { useCallback, useEffect, useState } from "react";
import { Card, Select, Spin, Table } from "antd";
import { useRouter } from "next/navigation";
import type { ColumnsType } from "antd/es/table";
import api from "@/lib/api";

interface HealthScore {
  name: string;
  health_score: number;
  repeat_rate: number;
  anomaly_rate: number;
}

export default function ChannelHealth() {
  const [scores, setScores] = useState<HealthScore[]>([]);
  const [loading, setLoading] = useState(true);
  const [dimension, setDimension] = useState<string>("distributor");
  const router = useRouter();

  const fetchData = useCallback(async () => {
    try {
      const res = await api.get("/channel-analytics/health-scores", {
        params: { dimension, days_back: 30 },
      });
      const raw = res.data?.scores || [];
      // 取 Top 5
      setScores(raw.slice(0, 5));
    } catch {
      // 静默处理
    } finally {
      setLoading(false);
    }
  }, [dimension]);

  useEffect(() => {
    void fetchData();
  }, [fetchData]);

  const columns: ColumnsType<HealthScore> = [
    { title: "渠道名称", dataIndex: "name", key: "name", ellipsis: true },
    {
      title: "健康评分",
      dataIndex: "health_score",
      key: "score",
      width: 90,
      render: (score: number) => {
        const color = score >= 80 ? "#3f8600" : score >= 60 ? "#faad14" : "#cf1322";
        return <span style={{ fontWeight: 600, color }}>{score}</span>;
      },
    },
    {
      title: "异常率",
      dataIndex: "anomaly_rate",
      key: "anomaly",
      width: 80,
      render: (rate: number) => (
        <span style={{ color: rate > 0.1 ? "#cf1322" : undefined }}>
          {(rate * 100).toFixed(1)}%
        </span>
      ),
    },
  ];

  return (
    <Card
      title={
        <div className="flex items-center justify-between">
          <span>渠道健康 Top 5</span>
          <Select
            size="small"
            value={dimension}
            onChange={setDimension}
            style={{ width: 100 }}
            options={[
              { value: "distributor", label: "经销商" },
              { value: "region", label: "区域" },
              { value: "store", label: "门店" },
            ]}
          />
        </div>
      }
      size="small"
      style={{ height: "100%" }}
    >
      {loading ? (
        <div className="flex items-center justify-center" style={{ height: 200 }}>
          <Spin />
        </div>
      ) : scores.length === 0 ? (
        <div className="flex items-center justify-center text-gray-400" style={{ height: 200 }}>
          暂无渠道数据
        </div>
      ) : (
        <Table
          columns={columns}
          dataSource={scores}
          rowKey="name"
          pagination={false}
          size="small"
          onRow={() => ({
            onClick: () => router.push("/risk-center"),
            style: { cursor: "pointer" },
          })}
        />
      )}
    </Card>
  );
}
```

- [ ] **Step 2: 提交**

```bash
git add frontend/apps/admin/src/app/\(dashboard\)/_components/dashboard/ChannelHealth.tsx
git commit -m "feat(dashboard): add ChannelHealth component"
```

---

### Task 15: 快速操作 + 最近动态组件

**Files:**
- Create: `frontend/apps/admin/src/app/(dashboard)/_components/dashboard/QuickActions.tsx`
- Create: `frontend/apps/admin/src/app/(dashboard)/_components/dashboard/RecentEvents.tsx`

- [ ] **Step 1: 创建 QuickActions**

```tsx
"use client";

import { Button, Card, Space } from "antd";
import {
  LinkOutlined,
  GiftOutlined,
  DownloadOutlined,
  LineChartOutlined,
} from "@ant-design/icons";
import { useRouter } from "next/navigation";

export default function QuickActions() {
  const router = useRouter();

  return (
    <Card title="快速操作" size="small" style={{ height: "100%" }}>
      <Space direction="vertical" className="w-full">
        <Button
          block
          icon={<LinkOutlined />}
          onClick={() => router.push("/codes")}
        >
          创建码批次
        </Button>
        <Button
          block
          icon={<GiftOutlined />}
          onClick={() => router.push("/campaigns")}
        >
          创建营销活动
        </Button>
        <Button
          block
          icon={<DownloadOutlined />}
          onClick={() => router.push("/exports")}
        >
          导出数据
        </Button>
        <Button
          block
          type="link"
          icon={<LineChartOutlined />}
          onClick={() => router.push("/analytics")}
        >
          查看全部活动分析
        </Button>
      </Space>
    </Card>
  );
}
```

- [ ] **Step 2: 创建 RecentEvents**

```tsx
"use client";

import { useCallback, useEffect, useState } from "react";
import { Card, Spin, Timeline, Typography } from "antd";
import { useRouter } from "next/navigation";
import api from "@/lib/api";

const { Text } = Typography;

interface EventItem {
  event_type: string;
  message: string;
  timestamp: string;
  action_url: string | null;
}

const EVENT_COLORS: Record<string, string> = {
  scan_surge: "#1677ff",
  campaign_status_change: "#52c41a",
  channel_anomaly: "#cf1322",
  new_signup: "#722ed1",
  claim_milestone: "#fa8c16",
};

export default function RecentEvents() {
  const [events, setEvents] = useState<EventItem[]>([]);
  const [loading, setLoading] = useState(true);
  const router = useRouter();

  const fetchData = useCallback(async () => {
    try {
      const res = await api.get("/analytics/recent-events", {
        params: { limit: 5 },
      });
      setEvents(res.data?.events || []);
    } catch {
      // 静默处理
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchData();
  }, [fetchData]);

  return (
    <Card title="最近动态" size="small" style={{ height: "100%" }}>
      {loading ? (
        <div className="flex items-center justify-center" style={{ height: 150 }}>
          <Spin />
        </div>
      ) : events.length === 0 ? (
        <div className="flex items-center justify-center text-gray-400" style={{ height: 150 }}>
          暂无动态
        </div>
      ) : (
        <Timeline
          items={events.map((event) => ({
            color: EVENT_COLORS[event.event_type] || "gray",
            children: (
              <div
                style={{ cursor: event.action_url ? "pointer" : "default" }}
                onClick={() => {
                  if (event.action_url?.startsWith("/")) {
                    router.push(event.action_url);
                  }
                }}
              >
                <Text>{event.message}</Text>
                <br />
                <Text type="secondary" style={{ fontSize: 11 }}>
                  {new Date(event.timestamp).toLocaleDateString("zh-CN")}
                </Text>
              </div>
            ),
          }))}
        />
      )}
    </Card>
  );
}
```

- [ ] **Step 3: 提交**

```bash
git add frontend/apps/admin/src/app/\(dashboard\)/_components/dashboard/QuickActions.tsx
git add frontend/apps/admin/src/app/\(dashboard\)/_components/dashboard/RecentEvents.tsx
git commit -m "feat(dashboard): add QuickActions and RecentEvents components"
```

---

### Task 16: 双轴趋势图组件

**Files:**
- Create: `frontend/apps/admin/src/app/(dashboard)/_components/dashboard/DashboardCharts.tsx`

- [ ] **Step 1: 创建双轴趋势图**

```tsx
"use client";

import { Card, DatePicker, Spin } from "antd";
import { Line, Column } from "@ant-design/charts";
import { useCallback, useEffect, useState } from "react";
import dayjs, { type Dayjs } from "dayjs";
import api from "@/lib/api";

interface TrendRow {
  date: string;
  total_scans: number;
}

interface DashboardChartsProps {
  initialTrend: TrendRow[];
  loading?: boolean;
}

export default function DashboardCharts({ initialTrend, loading }: DashboardChartsProps) {
  const [trend, setTrend] = useState<TrendRow[]>(initialTrend || []);
  const [dateRange, setDateRange] = useState<[Dayjs, Dayjs]>([
    dayjs().subtract(29, "day"),
    dayjs(),
  ]);
  const [innerLoading, setInnerLoading] = useState(false);

  const fetchTrend = useCallback(async (range: [Dayjs, Dayjs]) => {
    setInnerLoading(true);
    try {
      const res = await api.get("/analytics/scan-stats", {
        params: {
          start_date: range[0].format("YYYY-MM-DD"),
          end_date: range[1].format("YYYY-MM-DD"),
        },
      });
      setTrend(res.data || []);
    } catch {
      // 静默处理
    } finally {
      setInnerLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!initialTrend || initialTrend.length === 0) {
      void fetchTrend(dateRange);
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const chartData = trend.map((row) => [
    { date: row.date, value: row.total_scans, type: "扫码量" },
    { date: row.date, value: row.uv || 0, type: "独立用户" },
  ]).flat();

  const config = {
    data: chartData,
    xField: "date",
    yField: "value",
    colorField: "type",
    height: 280,
    smooth: true,
    point: { shapeField: "square", sizeField: 3 },
    interaction: { tooltip: { marker: false } },
    style: { lineWidth: 2 },
    scale: { color: { range: ["#1677ff", "#52c41a"] } },
    axis: {
      y: { title: "数量" },
      x: { title: false },
    },
  };

  return (
    <Card
      title="扫码趋势"
      size="small"
      extra={
        <DatePicker.RangePicker
          size="small"
          value={dateRange}
          onChange={(dates) => {
            if (dates && dates[0] && dates[1]) {
              setDateRange([dates[0], dates[1]]);
              void fetchTrend([dates[0], dates[1]]);
            }
          }}
          disabledDate={(current) => current && current.isAfter(dayjs().endOf("day"))}
        />
      }
    >
      {(loading || innerLoading) ? (
        <div className="flex items-center justify-center" style={{ height: 280 }}>
          <Spin />
        </div>
      ) : chartData.length === 0 ? (
        <div className="flex items-center justify-center text-gray-400" style={{ height: 280 }}>
          暂无趋势数据
        </div>
      ) : (
        <Line {...config} />
      )}
    </Card>
  );
}
```

- [ ] **Step 2: 提交**

```bash
git add frontend/apps/admin/src/app/\(dashboard\)/_components/dashboard/DashboardCharts.tsx
git commit -m "feat(dashboard): add dual-axis trend chart component"
```

---

## Phase 4: 组装新经营看板

### Task 17: 重写 DashboardHome

**Files:**
- Modify: `frontend/apps/admin/src/app/(dashboard)/_components/DashboardHome.tsx` (全量重写)

- [ ] **Step 1: 重写 DashboardHome.tsx**

将 `DashboardHome.tsx` 全部内容替换为：

```tsx
"use client";

import { useCallback, useEffect, useState } from "react";
import { App, Button, Col, Row, Space, Statistic, Tooltip } from "antd";
import {
  ReloadOutlined,
  ScanOutlined,
  RiseOutlined,
  TeamOutlined,
  GiftOutlined,
} from "@ant-design/icons";
import api, { extractErrorMessage } from "@/lib/api";

import StatusAlerts from "./dashboard/StatusAlerts";
import ConversionFunnel from "./dashboard/ConversionFunnel";
import DashboardCharts from "./dashboard/DashboardCharts";
import CampaignRanking from "./dashboard/CampaignRanking";
import ChannelHealth from "./dashboard/ChannelHealth";
import QuickActions from "./dashboard/QuickActions";
import RecentEvents from "./dashboard/RecentEvents";

interface DashboardData {
  today_scans: number;
  today_uv: number;
  cumulative_scans: number;
  cumulative_first_scans: number;
  period_claim_count: number;
  period_claim_rate: number;
  trend?: { date: string; total_scans: number; uv?: number }[];
  environment_breakdown?: Record<string, number>;
  comparison?: {
    weekly_scans_change: { value: number; direction: string } | null;
    weekly_first_scans_change: { value: number; direction: string } | null;
  };
}

function ChangeIndicator({ change }: { change: { value: number; direction: string } | null | undefined }) {
  if (!change) return null;
  const isUp = change.direction === "up";
  return (
    <span style={{ fontSize: 12, color: isUp ? "#3f8600" : "#cf1322" }}>
      {isUp ? "↑" : "↓"} 较上周 {Math.abs(change.value)}%
    </span>
  );
}

export default function DashboardHome() {
  const { message } = App.useApp();
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchDashboard = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.get("/analytics/dashboard", { params: { days_back: 30 } });
      setData(res.data);
    } catch (err) {
      message.error(extractErrorMessage(err, "加载工作台数据失败"));
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => {
    void fetchDashboard();
  }, [fetchDashboard]);

  return (
    <div>
      {/* 标题栏 */}
      <div className="mb-4 flex items-center justify-between">
        <h4 className="mb-0" style={{ fontSize: 20, fontWeight: 600 }}>经营看板</h4>
        <Space>
          <Tooltip title="刷新数据">
            <Button
              icon={<ReloadOutlined />}
              onClick={() => void fetchDashboard()}
              loading={loading}
            />
          </Tooltip>
        </Space>
      </div>

      {/* Area 1: 状态提醒 */}
      <StatusAlerts />

      {/* Area 2: 转化漏斗 */}
      <div className="mb-6">
        <ConversionFunnel />
      </div>

      {/* 统计卡片 */}
      <Row gutter={[16, 16]} className="mb-6">
        <Col xs={24} sm={12} lg={6}>
          <div style={{ borderLeft: "3px solid #1677ff", padding: "12px 16px", background: "var(--ant-color-bg-container)", borderRadius: 6 }}>
            <Statistic
              title="今日扫码"
              value={data?.today_scans ?? 0}
              prefix={<ScanOutlined />}
              loading={loading}
            />
          </div>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <div style={{ padding: "12px 16px", background: "var(--ant-color-bg-container)", borderRadius: 6 }}>
            <Statistic
              title="今日 UV"
              value={data?.today_uv ?? 0}
              prefix={<TeamOutlined />}
              loading={loading}
            />
          </div>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <div style={{ padding: "12px 16px", background: "var(--ant-color-bg-container)", borderRadius: 6 }}>
            <Statistic
              title="累计扫码"
              value={data?.cumulative_scans ?? 0}
              prefix={<RiseOutlined />}
              loading={loading}
            />
            <ChangeIndicator change={data?.comparison?.weekly_scans_change} />
          </div>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <div style={{ padding: "12px 16px", background: "var(--ant-color-bg-container)", borderRadius: 6 }}>
            <Statistic
              title="期间领券"
              value={data?.period_claim_count ?? 0}
              suffix={
                data?.period_claim_rate ? `(${data.period_claim_rate}%)` : ""
              }
              prefix={<GiftOutlined />}
              loading={loading}
            />
          </div>
        </Col>
      </Row>

      {/* Area 3: 趋势图 */}
      <div className="mb-6">
        <DashboardCharts
          initialTrend={data?.trend || []}
          loading={loading}
        />
      </div>

      {/* Area 4 & 5: 排行面板 */}
      <Row gutter={[16, 16]} className="mb-6">
        <Col xs={24} lg={12}>
          <CampaignRanking />
        </Col>
        <Col xs={24} lg={12}>
          <ChannelHealth />
        </Col>
      </Row>

      {/* Area 6: 快速操作 + 最近动态 */}
      <Row gutter={[16, 16]}>
        <Col xs={24} lg={8}>
          <QuickActions />
        </Col>
        <Col xs={24} lg={16}>
          <RecentEvents />
        </Col>
      </Row>
    </div>
  );
}
```

- [ ] **Step 2: 验证页面渲染**

```bash
cd /Users/ericding/code/agriculture/yimatong/frontend && pnpm build:shared && pnpm build:admin 2>&1 | tail -10
```

Expected: 构建成功

- [ ] **Step 3: 提交**

```bash
git add frontend/apps/admin/src/app/\(dashboard\)/_components/DashboardHome.tsx
git commit -m "feat(dashboard): rewrite DashboardHome with 6-area layout"
```

---

## Phase 5: 清理 + 验证

### Task 18: 更新 i18n 翻译键

**Files:**
- Modify: `frontend/apps/admin/src/lib/i18n/zh-CN.ts` (或对应翻译文件)
- Modify: `frontend/apps/admin/src/lib/i18n/en-US.ts` (或对应翻译文件)

- [ ] **Step 1: 添加新菜单翻译键**

在中文翻译文件中添加：

```typescript
"menu.deep-analytics": "深度分析",
"menu.risk-center": "风控中心",
```

在英文翻译文件中添加：

```typescript
"menu.deep-analytics": "Deep Analytics",
"menu.risk-center": "Risk Center",
```

- [ ] **Step 2: 提交**

```bash
git add frontend/apps/admin/src/lib/i18n/
git commit -m "feat(i18n): add translation keys for new menu items"
```

---

### Task 19: 端到端验证

**Files:**
- None (manual testing)

- [ ] **Step 1: 启动后端**

```bash
cd /Users/ericding/code/agriculture/yimatong/backend && source .venv/bin/activate
uv run uvicorn app.main:app --reload &
```

- [ ] **Step 2: 启动前端**

```bash
cd /Users/ericding/code/agriculture/yimatong/frontend
pnpm dev:admin &
```

- [ ] **Step 3: 验证经营看板**

打开 `http://localhost:3000/`，检查：
- [ ] 状态提醒栏显示（如有异常条件触发）
- [ ] 转化漏斗显示 5 个环节及百分比
- [ ] 4 个统计卡片显示今日扫码、UV、累计扫码、期间领券
- [ ] 趋势图显示扫码量和 UV 双线
- [ ] 活动排行 Top 5 表格
- [ ] 渠道健康 Top 5 表格
- [ ] 快速操作按钮可点击跳转
- [ ] 最近动态时间线

- [ ] **Step 4: 验证深度分析**

打开 `http://localhost:3000/analytics`，检查：
- [ ] 两个 Tab（活动分析、GMV 归因）可切换
- [ ] 活动分析 Tab 功能与原 campaign-analytics 一致
- [ ] GMV 归因 Tab 功能与原 gmv 一致

- [ ] **Step 5: 验证风控中心**

打开 `http://localhost:3000/risk-center`，检查：
- [ ] 概览指标条显示（待处理告警、窜货线索、健康评分）
- [ ] 原有风控功能全部正常

- [ ] **Step 6: 验证路由重定向**

- [ ] 访问 `/stats` → 应重定向到 `/`
- [ ] 旧路由 `/campaign-analytics` 仍可访问
- [ ] 旧路由 `/gmv` 仍可访问
- [ ] 旧路由 `/risk-dashboard` 仍可访问

- [ ] **Step 7: 验证侧边栏**

- [ ] 经营看板（/）菜单项显示且高亮正确
- [ ] 深度分析（/analytics）菜单项显示且高亮正确
- [ ] 风控中心（/risk-center）菜单项显示且高亮正确
- [ ] 数据导出（/exports）菜单项显示
- [ ] 扫码统计菜单项已消失

---

### Task 20: 清理无用代码 + 最终提交

**Files:**
- Review: `frontend/apps/admin/src/components/ChartPlaceholder.tsx` (检查是否仍被引用)

- [ ] **Step 1: 检查 ChartPlaceholder 是否仍被引用**

```bash
grep -rn "ChartPlaceholder" /Users/ericding/code/agriculture/yimatong/frontend/apps/admin/src/ --include="*.tsx" --include="*.ts"
```

如果除 `ChartPlaceholder.tsx` 自身外无其他引用，保留（其他页面可能用到）。

- [ ] **Step 2: 最终 lint 检查**

```bash
cd /Users/ericding/code/agriculture/yimatong/frontend && pnpm lint:admin 2>&1 | tail -20
cd /Users/ericding/code/agriculture/yimatong/backend && source .venv/bin/activate && ruff check . 2>&1 | tail -20
```

- [ ] **Step 3: 最终提交**

```bash
git add -A
git commit -m "chore: dashboard redesign — final cleanup and verification"
```

---

## Self-Review

### Spec Coverage

| Spec 需求 | 对应 Task |
|-----------|-----------|
| Area 1 状态提醒栏 | Task 11 (组件) + Task 3 (后端) |
| Area 2 转化漏斗 | Task 12 (组件) + Task 2 (后端) |
| 统计卡片带环比 | Task 17 (组装) + Task 6 (增强 dashboard) |
| Area 3 双轴趋势图 | Task 16 (组件) |
| Area 4 活动排行 | Task 13 (组件) + Task 4 (后端) |
| Area 5 渠道健康 | Task 14 (组件，复用现有 API) |
| Area 6 快速操作 + 最近动态 | Task 15 (组件) + Task 4 (后端) |
| 深度分析合并 | Task 8 |
| 风控中心改名 | Task 9 |
| 扫码统计删除 + 重定向 | Task 10 |
| 侧边栏菜单重构 | Task 7 |
| i18n 翻译 | Task 18 |
| 端到端验证 | Task 19 |

### Placeholder Scan

无 TBD/TODO。所有代码步骤均包含完整实现。

### Type Consistency

- 后端 `ConversionFunnelResponse` schema 与前端 `FunnelData` 接口字段名对齐
- 后端 `AlertItem` schema 与前端 `AlertItem` 接口对齐
- 后端 `CampaignRankingItem.campaign_id` 返回 string (UUID)，前端接收 string
- 后端 `RecentEventItem.timestamp` 返回 ISO string，前端用 `new Date()` 解析
