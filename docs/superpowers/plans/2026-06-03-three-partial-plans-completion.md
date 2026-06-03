# 三个部分完成计划补齐 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 补齐三个标记为 ⏸ Partial 的实施计划，使其达到完整状态。

**Architecture:** 前端改动为主（Admin Next.js + Ant Design 6），后端改动集中在 analytics 比较数据和渠道开关配置。按 Wave 1→2→3 顺序推进，每个 Wave 独立可提交。

**Tech Stack:** Next.js 16 + Ant Design 6 + Zustand（前端），FastAPI + SQLAlchemy 2.0 async（后端），Python pytest + Vitest（测试）

**关键发现（代码审计结果）：**
- SSE 指数退避重连**已实现**（`risk-dashboard/page.tsx:22-28`）
- 经销商门户前端**已存在**（`channel-portal/page.tsx`，138 行）
- 门店门户前端**已存在**（`store-portal/page.tsx`，111 行）
- 实际剩余工作量比预估大幅减少

---

## File Structure

### 前端修改
| 文件 | 操作 | 职责 |
|------|------|------|
| `frontend/apps/admin/src/app/(dashboard)/_components/DashboardHome.tsx` | Modify | 刷新按钮 + 表格行点击 + 同比对比 |
| `frontend/apps/admin/src/app/(dashboard)/stats/page.tsx` | Modify | 布局调整：日期选择器上移 |
| `frontend/apps/admin/src/app/(dashboard)/campaign-analytics/page.tsx` | Modify | 补齐 loading skeleton |
| `frontend/apps/admin/src/app/(dashboard)/risk-dashboard/page.tsx` | Modify | SSE 断线横幅提示 |
| `frontend/apps/admin/src/app/(dashboard)/channels/page.tsx` | Modify | 门店模块开关隐藏/显示 |
| `frontend/apps/admin/src/app/(dashboard)/channel-portal/page.tsx` | Modify | 增加窜货预警 Tab |

### 后端修改
| 文件 | 操作 | 职责 |
|------|------|------|
| `backend/app/services/analytics.py` | Modify | get_dashboard 增加同比对比数据 |
| `backend/app/api/v1/analytics.py` | Modify | dashboard 端点透传 comparison |
| `backend/app/services/channel.py` | Modify | 窜货线索创建时生成 RiskNotification |
| `backend/app/api/v1/channels.py` | Modify | 门店相关 API 检查 enabled_features |
| `backend/app/models/tenant.py` | None | 只读 enabled_features JSON 字段 |

---

## Wave 1: 工作台 UX 改进

### Task 1: Dashboard 刷新按钮

**Files:**
- Modify: `frontend/apps/admin/src/app/(dashboard)/_components/DashboardHome.tsx`

- [ ] **Step 1: 添加刷新按钮**

在 DashboardHome 组件中，找到标题区域（约 L76 附近的 `export default function`），添加 `handleRefresh` 函数和刷新按钮。

在 `return` 的标题行右侧，添加刷新按钮：

```tsx
// 在组件内，现有 useState 声明之后添加：
const [refreshing, setRefreshing] = useState(false);

const handleRefresh = async () => {
  setRefreshing(true);
  try {
    const params: Record<string, string> = {
      days_back: "30",
    };
    const { data } = await api.get("/analytics/dashboard", { params });
    setData(data);
    if (data?.trend) setTrend(data.trend);
    message.success("数据已刷新");
  } catch {
    message.error("刷新失败");
  } finally {
    setRefreshing(false);
  }
};
```

在标题行（`<Title level={4}>` 附近）添加按钮：

```tsx
<Button
  icon={<ReloadOutlined />}
  onClick={handleRefresh}
  loading={refreshing}
  size="small"
>
  刷新
</Button>
```

- [ ] **Step 2: 验证刷新按钮工作**

手动测试：打开 Dashboard，点击刷新按钮，确认 loading 状态和数据刷新。

- [ ] **Step 3: 提交**

```bash
git add frontend/apps/admin/src/app/\(dashboard\)/_components/DashboardHome.tsx
git commit -m "feat(dashboard): add refresh button to dashboard"
```

### Task 2: Dashboard 同比对比指标

**Files:**
- Modify: `backend/app/services/analytics.py:74-125`
- Modify: `backend/app/api/v1/analytics.py:35`
- Modify: `frontend/apps/admin/src/app/(dashboard)/_components/DashboardHome.tsx`

- [ ] **Step 1: 后端 — 修改 get_dashboard 返回对比数据**

在 `backend/app/services/analytics.py` 的 `get_dashboard` 函数中，在 return 之前添加同期对比计算：

```python
    # 同期对比：计算前一周期数据
    period_days = (today - seven_days_ago).days + 1  # 当前周期天数
    prev_start = seven_days_ago - timedelta(days=period_days)
    prev_end = seven_days_ago - timedelta(days=1)

    prev_result = await db.execute(
        select(
            func.coalesce(func.sum(DailyScanStats.total_scans), 0),
            func.coalesce(func.sum(DailyScanStats.first_scans), 0),
        ).where(
            DailyScanStats.tenant_id == tenant_id,
            DailyScanStats.date >= prev_start,
            DailyScanStats.date <= prev_end,
        )
    )
    prev_row = prev_result.one()
    prev_scans = int(prev_row[0])
    prev_first_scans = int(prev_row[1])

    def _calc_change(current: int, previous: int) -> dict | None:
        if previous == 0:
            return None
        pct = round((current - previous) / previous * 100, 1)
        return {"value": pct, "direction": "up" if pct > 0 else "down" if pct < 0 else "flat"}

    return {
        "today_scans": today_stats.total_scans if today_stats else 0,
        "today_uv": today_stats.uv if today_stats else 0,
        "cumulative_scans": cumulative_scans,
        "cumulative_first_scans": cumulative_first_scans,
        "trend": trend,
        "environment_breakdown": env_stats,
        "comparison": {
            "weekly_scans_change": _calc_change(
                sum(s["total_scans"] for s in trend), prev_scans
            ),
            "weekly_first_scans_change": _calc_change(
                sum(s["first_scans"] for s in trend), prev_first_scans
            ),
        },
    }
```

- [ ] **Step 2: 前端 — 显示对比指标**

在 DashboardHome 的统计卡片中，给 `today_scans` 和 `cumulative_first_scans` 卡片添加对比 footer：

```tsx
// 在组件内添加 comparison 状态：
const [comparison, setComparison] = useState<{
  weekly_scans_change: { value: number; direction: string } | null;
  weekly_first_scans_change: { value: number; direction: string } | null;
} | null>(null);

// 在 API 响应处理中保存 comparison：
// 找到 setData(data) 之后添加：
if (data?.comparison) setComparison(data.comparison);
```

在 Statistic 卡片下方添加对比标签（以今日扫码卡片为例）：

```tsx
{comparison?.weekly_scans_change && (
  <Text
    type={comparison.weekly_scans_change.direction === "up" ? "success" : "danger"}
    className="text-xs"
  >
    {comparison.weekly_scans_change.direction === "up" ? "↑" : "↓"}
    较上周 {Math.abs(comparison.weekly_scans_change.value)}%
  </Text>
)}
```

- [ ] **Step 3: 验证**

启动后端和前端，打开 Dashboard，确认统计卡片下方显示"较上周 +N%"或"较上周 -N%"。

- [ ] **Step 4: 提交**

```bash
git add backend/app/services/analytics.py backend/app/api/v1/analytics.py frontend/apps/admin/src/app/\(dashboard\)/_components/DashboardHome.tsx
git commit -m "feat(dashboard): add period-over-period comparison metrics"
```

### Task 3: 表格行点击导航

**Files:**
- Modify: `frontend/apps/admin/src/app/(dashboard)/_components/DashboardHome.tsx`

- [ ] **Step 1: 给活动表格和码批次表格添加 onRow**

在 DashboardHome 中，找到活动表格（Campaign 列 Table）和码批次表格（CodeBatch 列 Table），添加 `onRow` 配置：

```tsx
// 活动表格：
onRow={(record) => ({
  onClick: () => router.push(`/campaigns/${record.id}`),
  style: { cursor: "pointer" },
})}

// 码批次表格：
onRow={(record) => ({
  onClick: () => router.push(`/batches/${record.id}`),
  style: { cursor: "pointer" },
})}
```

- [ ] **Step 2: 验证**

点击活动表格行，确认跳转到活动详情页。点击码批次表格行，确认跳转到批次详情页。

- [ ] **Step 3: 提交**

```bash
git add frontend/apps/admin/src/app/\(dashboard\)/_components/DashboardHome.tsx
git commit -m "feat(dashboard): add table row click navigation"
```

### Task 4: SSE 断线提示横幅

**Files:**
- Modify: `frontend/apps/admin/src/app/(dashboard)/risk-dashboard/page.tsx`

- [ ] **Step 1: 在 AlertIndicator 组件渲染中添加断线提示**

SSE 重连逻辑已实现（指数退避）。只需在 `connected === false` 时显示断线横幅。

在 AlertIndicator 组件的 return 中，添加断线提示：

```tsx
{!connected && (
  <Alert
    type="warning"
    message="实时推送已断开，正在重连..."
    showIcon
    banner
    className="mb-4"
  />
)}
```

- [ ] **Step 2: 验证**

断开后端连接，确认风控看板顶部显示黄色横幅"实时推送已断开，正在重连..."，重连成功后横幅消失。

- [ ] **Step 3: 提交**

```bash
git add frontend/apps/admin/src/app/\(dashboard\)/risk-dashboard/page.tsx
git commit -m "feat(risk-dashboard): add SSE disconnection banner"
```

---

## Wave 2: 扫码统计 UX 改进

### Task 5: Stats 页面布局调整

**Files:**
- Modify: `frontend/apps/admin/src/app/(dashboard)/stats/page.tsx`

- [ ] **Step 1: 将日期选择器移到统计卡片上方**

当前布局：标题 → 日期选择器行 → 统计卡片 → 趋势图 → 表格。调整为：标题 → 日期选择器+导出行 → 统计卡片 → 趋势图 → 表格。

当前 `stats/page.tsx` 中日期选择器已在卡片上方（L115-130），但卡片紧跟其后。将导出按钮移到日期选择器同行，确认布局正确即可。当前布局已基本正确，只检查移动端响应式：

在 `<Space>` 外包一层 flex 容器确保移动端换行：

```tsx
<div className="mb-4 flex flex-wrap items-center gap-3">
  <RangePicker ... />
  <Button icon={<DownloadOutlined />} ... />
</div>
```

- [ ] **Step 2: 提交**

```bash
git add frontend/apps/admin/src/app/\(dashboard\)/stats/page.tsx
git commit -m "fix(stats): improve date picker layout responsiveness"
```

### Task 6: Campaign Analytics 加载态

**Files:**
- Modify: `frontend/apps/admin/src/app/(dashboard)/campaign-analytics/page.tsx`

- [ ] **Step 1: 审查并补齐 loading skeleton**

读取 `campaign-analytics/page.tsx`，检查所有数据卡片是否都有 loading 状态。对于缺失的：

```tsx
// 给每个 Card 添加 loading 属性：
<Card loading={loading}>
  <Statistic ... />
</Card>
```

对于趋势图表区域，在 loading 时显示 Skeleton：

```tsx
{loading ? (
  <Card title="活动扫码趋势" size="small">
    <Skeleton active paragraph={{ rows: 4 }} />
  </Card>
) : data.length > 0 ? (
  <Card title="活动扫码趋势" size="small">
    <ScanTrendChart ... />
  </Card>
) : null}
```

- [ ] **Step 2: 提交**

```bash
git add frontend/apps/admin/src/app/\(dashboard\)/campaign-analytics/page.tsx
git commit -m "fix(campaign-analytics): add loading skeletons to all data cards"
```

### Task 7: SSE Ticket 认证验证

**Files:**
- Verify: `backend/app/api/v1/risk_dashboard.py:160-173`
- Create: `backend/tests/test_api/test_sse_ticket.py`

- [ ] **Step 1: 读取现有 SSE ticket 逻辑**

读取 `risk_dashboard.py` 的 `create_sse_ticket` 和 `alert_stream` 函数，确认 ticket 生成和验证流程。

- [ ] **Step 2: 编写 SSE ticket 测试**

```python
# backend/tests/test_api/test_sse_ticket.py
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_sse_ticket(client: AsyncClient, auth_headers: dict):
    """SSE ticket 端点应返回有效 ticket"""
    resp = await client.post("/api/v1/risk-dashboard/alerts/ticket", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "ticket" in data
    assert len(data["ticket"]) > 0


@pytest.mark.asyncio
async def test_alert_stream_requires_ticket(client: AsyncClient):
    """SSE stream 端点无 ticket 应拒绝"""
    resp = await client.get("/api/v1/risk-dashboard/alerts/stream")
    assert resp.status_code in (400, 401, 403)


@pytest.mark.asyncio
async def test_alert_stream_invalid_ticket(client: AsyncClient):
    """SSE stream 端点无效 ticket 应拒绝"""
    resp = await client.get(
        "/api/v1/risk-dashboard/alerts/stream?ticket=invalid_ticket"
    )
    assert resp.status_code in (400, 401, 403)
```

- [ ] **Step 3: 运行测试**

```bash
cd backend && python -m pytest tests/test_api/test_sse_ticket.py -v
```

- [ ] **Step 4: 提交**

```bash
git add backend/tests/test_api/test_sse_ticket.py
git commit -m "test(sse): add SSE ticket authentication tests"
```

---

## Wave 3: 渠道闭环补齐

### Task 8: 门店模块开关（后端）

**Files:**
- Modify: `backend/app/api/v1/channels.py`

- [ ] **Step 1: 在门店相关 API 中检查 enabled_features**

在 `channels.py` 中，给门店相关端点添加开关检查。找到 `store_portal_summary_endpoint` 和门店 CRUD 端点，添加依赖检查：

```python
# 在门店相关端点函数体内添加检查：
from app.core.dependencies import get_current_tenant

# 检查门店模块是否启用
tenant = await get_current_tenant(...)  # 或从依赖注入获取
enabled = tenant.enabled_features or {}
if not enabled.get("channel_store", False):
    raise HTTPException(status_code=403, detail="门店模块未启用")
```

也可抽取为依赖函数复用：

```python
async def require_store_enabled(db: AsyncSession = Depends(get_db), tenant_id: uuid.UUID = Depends(get_current_tenant)) -> None:
    """检查租户是否启用了门店模块"""
    from app.services.tenant import get_tenant
    tenant = await get_tenant(db, tenant_id)
    enabled = tenant.enabled_features or {}
    if not enabled.get("channel_store", False):
        raise HTTPException(status_code=403, detail="门店模块未启用")
```

- [ ] **Step 2: 提交**

```bash
git add backend/app/api/v1/channels.py
git commit -m "feat(channels): add channel_store feature toggle for store module"
```

### Task 9: 门店模块开关（前端）

**Files:**
- Modify: `frontend/apps/admin/src/app/(dashboard)/channels/page.tsx`

- [ ] **Step 1: 根据租户配置隐藏门店 Tab**

渠道管理页面使用 Ant Design Tabs。读取当前租户的 `enabled_features`，隐藏门店 Tab：

```tsx
// 在渠道管理页面组件中添加：
const [tenantFeatures, setTenantFeatures] = useState<Record<string, boolean>>({});

useEffect(() => {
  api.get("/tenants/me").then(({ data }) => {
    setTenantFeatures(data?.enabled_features || {});
  }).catch(() => {});
}, []);

// 过滤 tabs 数组，隐藏门店 tab：
const visibleTabs = allTabs.filter(tab => {
  if (tab.key === "store") return tenantFeatures.channel_store === true;
  return true;
});
```

- [ ] **Step 2: 提交**

```bash
git add frontend/apps/admin/src/app/\(dashboard\)/channels/page.tsx
git commit -m "feat(channels): hide store tab based on tenant enabled_features"
```

### Task 10: 窜货预警闭环（后端）

**Files:**
- Modify: `backend/app/services/channel.py`（或窜货线索创建逻辑所在文件）

- [ ] **Step 1: 窜货线索创建时自动生成 RiskNotification**

找到创建 `DiversionClue` 的服务函数，在创建完成后添加通知生成：

```python
from app.models.risk import RiskNotification

# 在 DiversionClue 创建后：
notification = RiskNotification(
    tenant_id=tenant_id,
    notification_type="diversion_alert",
    title=f"疑似窜货：{clue.public_id}",
    detail=f"预期区域 {clue.expected_region}，实际扫码城市 {clue.detected_city}",
    risk_rule_id=None,
    campaign_id=None,
    code_item_id=clue.code_item_id,
    read=False,
)
db.add(notification)
await db.flush()
```

- [ ] **Step 2: 提交**

```bash
git add backend/app/services/channel.py
git commit -m "feat(channels): auto-create risk notification on diversion clue"
```

### Task 11: 经销商门户增加窜货预警 Tab

**Files:**
- Modify: `frontend/apps/admin/src/app/(dashboard)/channel-portal/page.tsx`

- [ ] **Step 1: 在经销商门户添加预警记录 Tab**

在现有 channel-portal 页面的"区域覆盖"和"最近收货流向"之间，添加窜货预警卡片：

```tsx
// 添加预警数据状态：
const [alerts, setAlerts] = useState<Array<{ id: string; title: string; detail: string; read: boolean }>>([]);

// 在 useEffect 中同时获取预警：
api.get("/risk-notifications?notification_type=diversion_alert&page_size=10")
  .then(({ data }) => {
    if (active && Array.isArray(data)) setAlerts(data);
    // 或根据实际分页响应结构调整
  })
  .catch(() => {});
```

添加预警卡片 UI：

```tsx
<Card title="窜货预警" size="small" className="mb-5">
  {alerts.length > 0 ? (
    <Table
      columns={[
        { title: "预警", dataIndex: "title" },
        { title: "详情", dataIndex: "detail" },
        {
          title: "状态",
          dataIndex: "read",
          render: (v: boolean) => v ? <Tag>已读</Tag> : <Tag color="red">未读</Tag>,
        },
      ]}
      dataSource={alerts}
      rowKey="id"
      pagination={false}
      loading={loading}
    />
  ) : (
    <Empty description="暂无窜货预警" />
  )}
</Card>
```

- [ ] **Step 2: 提交**

```bash
git add frontend/apps/admin/src/app/\(dashboard\)/channel-portal/page.tsx
git commit -m "feat(channel-portal): add diversion alert tab to distributor portal"
```

### Task 12: 更新三个计划的完成度标记

**Files:**
- Modify: `docs/superpowers/plans/2026-06-02-dashboard-ux-improvements.md`
- Modify: `docs/superpowers/plans/2026-06-02-scan-stats-ux-improvements.md`
- Modify: `docs/superpowers/plans/2026-06-01-channel-region-closed-loop.md`
- Modify: `docs/MANIFEST.md`

- [ ] **Step 1: 更新计划头部状态标记**

将三个计划的 Status 从 ⏸ 改为 ✅：

- `dashboard-ux-improvements.md`: `> **Status:** ✅ Completed`
- `scan-stats-ux-improvements.md`: `> **Status:** ✅ Completed`
- `channel-region-closed-loop.md`: `> **Status:** ✅ Completed`

更新 MANIFEST.md 中对应行的状态。

- [ ] **Step 2: 提交**

```bash
git add docs/superpowers/plans/ docs/MANIFEST.md
git commit -m "docs: mark three partial plans as completed"
```
