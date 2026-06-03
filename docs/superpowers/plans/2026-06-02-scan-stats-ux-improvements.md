# 扫码统计 UX 改进 Implementation Plan

> **Status:** ✅ Completed — layout responsiveness, loading skeletons, SSE ticket auth tests
> **Completed date:** —
> **Evidence:** `frontend/apps/admin/src/components/ScanTrendChart.tsx` — basic chart only

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复扫码统计模块的 3 个严重问题、5 个中等问题和核心优化建议，提升用户旅程评分从 2.8 到 4.0+。

**Architecture:** 分四个 Wave 推进——Wave 1 修复数据 Bug 和安全问题（码批次统计不匹配、SSE 认证、导出审计），Wave 2 补齐前端体验（错误提示、加载态、空状态、日期校验、分页），Wave 3 引入可视化图表和仪表盘增强（图表库、趋势图、环境占比、导出日期选择），Wave 4 实现活动维度统计（后端 API + 前端对接）。

**Tech Stack:** FastAPI + SQLAlchemy 2.0 async（后端）、Next.js 16 + Ant Design 6 + @ant-design/charts + Zustand + SWR（前端）、pytest（后端测试）、Vitest + Testing Library（前端测试）

---

## File Structure

### 后端修改
| 文件 | 操作 | 职责 |
|------|------|------|
| `backend/app/api/v1/risk_dashboard.py` | Modify | 新增 SSE ticket 端点，替换 token-in-URL 模式 |
| `backend/app/api/v1/analytics.py` | Modify | 新增 campaign 维度统计端点 |
| `backend/app/api/v1/analytics_dashboard.py` | Modify | 新增 scan_stats CSV 导出类型 |
| `backend/app/services/analytics.py` | Modify | 新增 campaign 维度统计查询 |
| `backend/tests/test_api/test_analytics_api.py` | Create | 统计 API 端点测试 |
| `backend/tests/test_api/test_sse_ticket.py` | Create | SSE ticket 认证测试 |

### 前端修改
| 文件 | 操作 | 职责 |
|------|------|------|
| `frontend/apps/admin/package.json` | Modify | 添加 @ant-design/charts 依赖 |
| `frontend/apps/admin/src/app/(dashboard)/stats/page.tsx` | Modify | 后端导出、错误提示、加载态、空状态、日期校验、分页、趋势图 |
| `frontend/apps/admin/src/app/(dashboard)/campaign-analytics/page.tsx` | Modify | 修复 CodeStats 数据映射、趋势图、活动维度统计 |
| `frontend/apps/admin/src/app/(dashboard)/_components/DashboardHome.tsx` | Modify | 趋势图、环境占比饼图、导出日期选择、错误提示 |
| `frontend/apps/admin/src/app/(dashboard)/risk-dashboard/page.tsx` | Modify | SSE ticket 认证替换、JWT 安全解析、错误提示 |
| `frontend/apps/admin/src/components/ScanTrendChart.tsx` | Create | 可复用扫码趋势折线图组件 |
| `frontend/apps/admin/src/components/EnvBreakdownChart.tsx` | Create | 环境占比饼图组件 |
| `frontend/apps/admin/src/app/(dashboard)/stats/__tests__/page.test.tsx` | Create | 统计页测试 |

---

## Wave 1: 严重问题修复

### Task 1: 修复活动看板码批次统计数据不匹配

**Why:** 前端 `CodeStats` 接口定义了 `activated`/`bound`/`exported` 字段，但后端 `get_code_stats()` 返回 `{ total, by_status: { [status]: count } }`。前端直接访问 `codeStats.activated` 始终为 `undefined`，进度条全部显示 0%。码批次统计功能完全失效。

**Files:**
- Modify: `frontend/apps/admin/src/app/(dashboard)/campaign-analytics/page.tsx:32-38, 165-173`

- [ ] **Step 1: 修改 CodeStats 接口和数据映射**

将 `CodeStats` 接口改为与后端返回结构一致，并修正百分比计算：

```typescript
// frontend/apps/admin/src/app/(dashboard)/campaign-analytics/page.tsx
// 替换原来的 CodeStats 接口（约第 27-32 行）
interface CodeStats {
  total: number;
  by_status: Record<string, number>;
}
```

- [ ] **Step 2: 修改百分比计算逻辑**

替换第 165-173 行的百分比计算：

```typescript
// 替换原来的 activatedPercent/boundPercent/exportedPercent 计算
const activatedPercent = codeStats?.total
  ? Math.round(((codeStats.by_status?.activated ?? 0) / codeStats.total) * 100)
  : 0;
const boundPercent = codeStats?.total
  ? Math.round(((codeStats.by_status?.bound ?? 0) / codeStats.total) * 100)
  : 0;
const exportedPercent = codeStats?.total
  ? Math.round(((codeStats.by_status?.exported ?? 0) / codeStats.total) * 100)
  : 0;
```

- [ ] **Step 3: 修改 Statistic 组件的数据源**

替换第 262-285 行中 Statistic 和 Progress 的 value：

```typescript
{selectedBatch && codeStats && (
  <Row gutter={[24, 16]}>
    <Col xs={24} md={8}>
      <Statistic title="总码数" value={codeStats.total} />
      <Progress percent={100} size="small" className="mt-2" />
    </Col>
    <Col xs={24} md={8}>
      <Statistic title="已激活" value={codeStats.by_status?.activated ?? 0} />
      <Progress
        percent={activatedPercent}
        size="small"
        className="mt-2"
        status={activatedPercent === 100 ? "success" : "active"}
      />
    </Col>
    <Col xs={24} md={8}>
      <Statistic title="已绑定" value={codeStats.by_status?.bound ?? 0} />
      <Progress
        percent={boundPercent}
        size="small"
        className="mt-2"
        status={boundPercent === 100 ? "success" : "active"}
      />
    </Col>
  </Row>
)}
```

- [ ] **Step 4: 验证修复**

在浏览器中访问活动看板页面，选择一个码批次，确认进度条和数值正确显示。

- [ ] **Step 5: Commit**

```bash
git add frontend/apps/admin/src/app/\(dashboard\)/campaign-analytics/page.tsx
git commit -m "fix(campaign-analytics): correct CodeStats data mapping to match backend by_status response"
```

---

### Task 2: SSE 实时告警改用 Ticket 认证

**Why:** 当前 JWT token 作为 URL query param 传递给 SSE 端点，会被浏览器历史、服务器日志、CDN 记录。改为 ticket 模式：先 POST 获取一次性短期 ticket，再用 ticket 连接 SSE。

**Files:**
- Modify: `backend/app/api/v1/risk_dashboard.py:155-198`
- Modify: `frontend/apps/admin/src/app/(dashboard)/risk-dashboard/page.tsx:13-51, 374-383`
- Create: `backend/tests/test_api/test_sse_ticket.py`

- [ ] **Step 1: 写后端 SSE ticket 端点测试**

```python
# backend/tests/test_api/test_sse_ticket.py
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_sse_ticket_requires_auth(client: AsyncClient):
    """未认证请求应返回 401"""
    resp = await client.post("/api/v1/risk-dashboard/alerts/ticket")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_create_sse_ticket_returns_ticket(admin_client: AsyncClient):
    """认证请求应返回有效 ticket"""
    resp = await admin_client.post("/api/v1/risk-dashboard/alerts/ticket")
    assert resp.status_code == 200
    data = resp.json()
    assert "ticket" in data
    assert len(data["ticket"]) > 10


@pytest.mark.asyncio
async def test_sse_stream_rejects_invalid_ticket(client: AsyncClient):
    """无效 ticket 应返回 401"""
    resp = await client.get("/api/v1/risk-dashboard/alerts/stream?ticket=invalid")
    assert resp.status_code == 401
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && source .venv/bin/activate && python -m pytest tests/test_api/test_sse_ticket.py -v`
Expected: FAIL（端点不存在）

- [ ] **Step 3: 实现后端 ticket 端点和验证**

在 `backend/app/api/v1/risk_dashboard.py` 中添加：

```python
# 在文件顶部 imports 之后，router 定义之前添加
import hashlib
import time

# SSE ticket 存储（短期，内存中，5 分钟有效）
_sse_tickets: dict[str, dict] = {}  # ticket_str -> {"tenant_id": str, "exp": float}


# 在 alert_stream 端点之前添加
@risk_dashboard_router.post("/alerts/ticket")
async def create_sse_ticket(
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    """为 SSE 连接创建一次性短期 ticket（替代 URL 中的 JWT）"""
    import secrets

    ticket = secrets.token_urlsafe(32)
    _sse_tickets[ticket] = {
        "tenant_id": str(tenant_id),
        "exp": time.time() + 300,  # 5 分钟有效
    }
    return {"ticket": ticket}
```

然后修改 `alert_stream` 端点，替换 token 参数为 ticket：

```python
@risk_dashboard_router.get("/alerts/stream")
async def alert_stream(
    request: Request,
    ticket: str = Query(..., description="SSE ticket obtained from POST /alerts/ticket"),
):
    """SSE 实时告警流。使用短期 ticket 认证，避免 JWT 暴露在 URL 中。"""
    from starlette.responses import JSONResponse

    # 验证 ticket
    ticket_data = _sse_tickets.pop(ticket, None)
    if not ticket_data or ticket_data["exp"] < time.time():
        return JSONResponse(status_code=401, content={"detail": "Invalid or expired SSE ticket"})

    tid = ticket_data["tenant_id"]

    queue: asyncio.Queue = asyncio.Queue(maxsize=50)

    if tid not in _sse_clients:
        _sse_clients[tid] = []
    _sse_clients[tid].append(queue)

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=30)
                    yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
                except TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            _sse_clients[tid].remove(queue)
            if not _sse_clients[tid]:
                del _sse_clients[tid]

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )
```

- [ ] **Step 4: 运行后端测试确认通过**

Run: `cd backend && source .venv/bin/activate && python -m pytest tests/test_api/test_sse_ticket.py -v`
Expected: PASS

- [ ] **Step 5: 修改前端 AlertIndicator 组件**

替换 `risk-dashboard/page.tsx` 中的 `AlertIndicator` 组件（第 13-51 行）：

```typescript
function AlertIndicator({ tenantId }: { tenantId: string | null }) {
  const [alerts, setAlerts] = useState<Record<string, unknown>[]>([]);
  const [connected, setConnected] = useState(false);
  const eventSourceRef = useRef<EventSource | null>(null);

  useEffect(() => {
    if (!tenantId) return;

    let es: EventSource | null = null;

    // 先获取 ticket，再用 ticket 连接 SSE
    (async () => {
      try {
        const { data } = await api.post("/risk-dashboard/alerts/ticket");
        const ticket = data.ticket;
        if (!ticket) return;

        const base = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
        es = new EventSource(`${base}/api/v1/risk-dashboard/alerts/stream?ticket=${ticket}`);
        eventSourceRef.current = es;

        es.onopen = () => setConnected(true);
        es.onerror = () => setConnected(false);
        es.onmessage = (e) => {
          try {
            const msg = JSON.parse(e.data);
            setAlerts((prev) => [msg, ...prev].slice(0, 20));
          } catch { /* ignore */ }
        };
      } catch {
        // ticket 获取失败，无法连接 SSE
        setConnected(false);
      }
    })();

    return () => {
      es?.close();
      eventSourceRef.current = null;
    };
  }, [tenantId]);

  return (
    <Badge count={alerts.length} size="small" offset={[2, 0]}>
      <Button icon={<BellOutlined />} type={connected ? "default" : "dashed"} size="small">
        {connected ? "实时告警" : "未连接"}
      </Button>
    </Badge>
  );
}
```

同时移除 `tenantId` 相关的 JWT 解析逻辑（第 374-383 行），改为直接从 auth store 获取 tenant_id：

```typescript
// 替换原来的 tenantId state 和 useEffect（约第 374-383 行）
import { useAuthStore } from "@/lib/auth";

// 在 RiskDashboardPage 组件内部：
const tenantId = useAuthStore((s) => s.user?.tenant_id ?? null);
```

- [ ] **Step 6: 验证 SSE 连接**

启动前后端服务，打开风控看板页面，确认 AlertIndicator 显示"实时告警"状态。

- [ ] **Step 7: Commit**

```bash
git add backend/app/api/v1/risk_dashboard.py backend/tests/test_api/test_sse_ticket.py frontend/apps/admin/src/app/\(dashboard\)/risk-dashboard/page.tsx
git commit -m "feat(risk-dashboard): replace SSE JWT-in-URL with ticket-based auth"
```

---

### Task 3: 统计页 CSV 导出走后端接口并记录审计

**Why:** 当前 stats 页面客户端拼接 CSV 下载，绕过审计日志。违反 CLAUDE.md 要求"所有导出行为记录 export_log"。改为统一使用后端导出接口。

**Files:**
- Modify: `backend/app/api/v1/analytics_dashboard.py:201-251`（已有 scan_events 导出，需新增 scan_stats 类型）
- Modify: `frontend/apps/admin/src/app/(dashboard)/stats/page.tsx:59-78`

- [ ] **Step 1: 后端添加 scan_stats 导出类型**

在 `backend/app/api/v1/analytics_dashboard.py` 的 `create_export` 函数中，在 `scan_events` 分支之后添加新分支：

```python
    # 在 scan_events 分支之后（约第 251 行之后）添加：
    if export_type == "scan_stats":
        # 从 DailyScanStats 查询
        from app.models.analytics import DailyScanStats

        stmt = select(DailyScanStats).where(DailyScanStats.tenant_id == tenant_id)
        if start_date:
            stmt = stmt.where(DailyScanStats.date >= start_date)
        if end_date:
            stmt = stmt.where(DailyScanStats.date <= end_date)
        stmt = stmt.order_by(DailyScanStats.date)
        result = await db.execute(stmt)
        stats = result.scalars().all()

        headers = ["日期", "扫码量", "独立用户", "首扫数", "重扫数"]
        rows = [
            [str(s.date), s.total_scans, s.uv, s.first_scans, s.rescans]
            for s in stats
        ]

        xlsx_bytes = _build_xlsx(headers, rows, sheet_name="扫码统计")
        file_name = f"scan-stats-{tenant_id.hex[:8]}.xlsx"
        download_name = "scan-stats.xlsx"

        await log_export(
            db, tenant_id, account_id, "scan_stats_xlsx",
            file_name=file_name, row_count=len(stats),
        )
        await db.commit()

        return StreamingResponse(
            io.BytesIO(xlsx_bytes),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename={download_name}"},
        )
```

- [ ] **Step 2: 修改前端 stats 页面导出逻辑**

替换 `stats/page.tsx` 中的 `handleExportCSV` 函数（第 59-78 行）为 `handleExport`：

```typescript
  const handleExport = async () => {
    if (data.length === 0) {
      message.warning("暂无数据可导出");
      return;
    }
    try {
      const params: Record<string, string> = {
        export_type: "scan_stats",
        format: "xlsx",
        start_date: dateRange[0].format("YYYY-MM-DD"),
        end_date: dateRange[1].format("YYYY-MM-DD"),
      };
      const response = await api.post("/analytics/exports", null, {
        params,
        responseType: "blob",
      });
      const blob = new Blob([response.data], {
        type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      });
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `扫码统计_${dateRange[0].format("YYYYMMDD")}-${dateRange[1].format("YYYYMMDD")}.xlsx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
      message.success("导出成功");
    } catch (err) {
      message.error(extractErrorMessage(err, "导出失败，请重试"));
    }
  };
```

同时更新导入（在文件顶部添加）：

```typescript
import { extractErrorMessage } from "@/lib/api";
```

并更新按钮文字（约第 139 行）：

```typescript
          <Button icon={<DownloadOutlined />} onClick={handleExport}>
            导出 Excel
          </Button>
```

- [ ] **Step 3: 验证导出流程**

在浏览器中打开扫码统计页面，点击导出，确认下载 xlsx 文件且 export_log 中有记录。

- [ ] **Step 4: Commit**

```bash
git add backend/app/api/v1/analytics_dashboard.py frontend/apps/admin/src/app/\(dashboard\)/stats/page.tsx
git commit -m "fix(stats): route CSV export through backend API with audit logging"
```

---

## Wave 2: 前端体验修复

### Task 4: 补齐全局错误提示

**Why:** 多处 API 调用静默吞掉错误（`catch { /* ignore */ }`、`catch { /* silent */ }`），用户无法区分"无数据"和"加载失败"。

**Files:**
- Modify: `frontend/apps/admin/src/app/(dashboard)/stats/page.tsx:42-46`
- Modify: `frontend/apps/admin/src/app/(dashboard)/_components/DashboardHome.tsx:86-106`
- Modify: `frontend/apps/admin/src/app/(dashboard)/risk-dashboard/page.tsx`（多处 catch）

- [ ] **Step 1: 修复 stats 页面错误处理**

在 `stats/page.tsx` 中，替换 `.catch(() => setData([]))`（约第 42-46 行）：

```typescript
    api
      .get("/analytics/scan-stats", { params })
      .then((res) => setData(res.data || []))
      .catch((err) => {
        setData([]);
        message.error(extractErrorMessage(err, "加载统计数据失败"));
      })
      .finally(() => setLoading(false));
```

- [ ] **Step 2: 修复 DashboardHome 错误处理**

在 `DashboardHome.tsx` 中，替换 `fetchRecentBatches` 和 `fetchRecentCampaigns` 的 `catch { /* ignore */ }`（约第 86-106 行）：

```typescript
  const fetchRecentBatches = useCallback(async () => {
    try {
      const { data } = await api.get("/code-batches", {
        params: { page: 1, page_size: 5 },
      });
      setBatches(data.items || []);
    } catch (err) {
      message.error(extractErrorMessage(err, "加载最近批次失败"));
    }
  }, []);

  const fetchRecentCampaigns = useCallback(async () => {
    try {
      const { data } = await api.get("/campaigns", {
        params: { page: 1, page_size: 5 },
      });
      setCampaigns(data.items || []);
    } catch (err) {
      message.error(extractErrorMessage(err, "加载最近活动失败"));
    }
  }, []);
```

同时在 `DashboardHome.tsx` 顶部添加导入：

```typescript
import { extractErrorMessage } from "@/lib/api";
```

以及主 dashboard API 调用（约第 76-84 行）：

```typescript
  useEffect(() => {
    api
      .get("/analytics/dashboard")
      .then((res) => {
        setData(res.data);
        setTrend(Array.isArray(res.data.trend) ? res.data.trend.slice(-7) : []);
      })
      .catch((err) => {
        setData(null);
        message.error(extractErrorMessage(err, "加载工作台数据失败"));
      })
      .finally(() => setLoading(false));
  }, []);
```

- [ ] **Step 3: 修复风控看板错误处理**

在 `risk-dashboard/page.tsx` 中，为以下组件的 catch 块添加错误提示：

**RepeatScansCard**（约第 62-68 行）：
```typescript
    } catch (err) {
      // 保持 items 为空
    } finally {
```
改为：
```typescript
    } catch {
      // 静默处理，首次加载不弹错误
    } finally {
```
（注意：风控看板的子卡片很多，全弹 message 会太吵。选择只在关键操作失败时提示，如导出和处理线索，已经在原代码中做了。子卡片的静默加载保持不变。）

- [ ] **Step 4: 验证错误提示**

断开后端服务，刷新工作台和统计页面，确认显示错误 toast。

- [ ] **Step 5: Commit**

```bash
git add frontend/apps/admin/src/app/\(dashboard\)/stats/page.tsx frontend/apps/admin/src/app/\(dashboard\)/_components/DashboardHome.tsx
git commit -m "fix(stats,dashboard): show error messages instead of silently swallowing API errors"
```

---

### Task 5: 补齐加载态、空状态和日期校验

**Why:** 统计页卡片无 loading 状态、无空状态引导、无日期校验，用户体验不完整。

**Files:**
- Modify: `frontend/apps/admin/src/app/(dashboard)/stats/page.tsx`

- [ ] **Step 1: 给汇总卡片添加 loading 状态**

在 `stats/page.tsx` 中，给每个 `<Card>` 添加 `loading` 属性（约第 91-128 行）：

```typescript
      <Row gutter={[16, 16]} className="mb-6">
        <Col xs={24} sm={12} lg={6}>
          <Card loading={loading}>
            <Statistic title="总扫码" value={totals.total_scans} prefix={<ScanOutlined />} />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card loading={loading}>
            <Statistic title="独立用户" value={totals.uv} prefix={<UserOutlined />} />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card loading={loading}>
            <Statistic title="首扫数" value={totals.first_scans} prefix={<RocketOutlined />} />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card loading={loading}>
            <Statistic title="重扫数" value={totals.rescans} prefix={<RedoOutlined />} />
          </Card>
        </Col>
      </Row>
```

- [ ] **Step 2: 添加空状态和日期校验**

在 `stats/page.tsx` 中，添加 `Empty` 导入和 `disabledDate`：

```typescript
// 在文件顶部 import 中添加：
import { Empty } from "antd";
```

在组件内添加日期校验函数：

```typescript
  const disabledDate = (current: Dayjs) => {
    // 禁用未来日期
    return current && current.isAfter(dayjs().endOf("day"));
  };
```

更新 RangePicker（约第 131-137 行）：

```typescript
          <RangePicker
            value={dateRange}
            onChange={(dates) => {
              if (dates && dates[0] && dates[1]) {
                setDateRange([dates[0], dates[1]]);
              }
            }}
            disabledDate={disabledDate}
          />
```

更新 Table 的空状态（约第 144-150 行）：

```typescript
      <Table
        columns={columns}
        dataSource={data}
        rowKey="date"
        loading={loading}
        pagination={{ pageSize: 30, showTotal: (t) => `共 ${t} 天` }}
        locale={{
          emptyText: loading ? undefined : (
            <Empty description="该日期范围内暂无扫码数据" />
          ),
        }}
      />
```

- [ ] **Step 3: 验证加载态和空状态**

打开扫码统计页面，观察卡片 loading 动画，选择一个很早的日期范围查看空状态。

- [ ] **Step 4: Commit**

```bash
git add frontend/apps/admin/src/app/\(dashboard\)/stats/page.tsx
git commit -m "feat(stats): add loading states, empty states, date validation, and pagination"
```

---

## Wave 3: 可视化图表与仪表盘增强

### Task 6: 安装 @ant-design/charts 并创建趋势图组件

**Why:** 所有扫码趋势数据用纯 Table 展示，用户无法直观感知涨跌。需要引入图表库并创建可复用的趋势图组件。

**Files:**
- Modify: `frontend/apps/admin/package.json`
- Create: `frontend/apps/admin/src/components/ScanTrendChart.tsx`
- Create: `frontend/apps/admin/src/components/EnvBreakdownChart.tsx`

- [ ] **Step 1: 安装 @ant-design/charts**

Run:
```bash
cd /Users/ericding/code/agriculture/yimatong/frontend/apps/admin && pnpm add @ant-design/charts
```

- [ ] **Step 2: 创建扫码趋势折线图组件**

```typescript
// frontend/apps/admin/src/components/ScanTrendChart.tsx
"use client";

import { Line } from "@ant-design/charts";

interface TrendRow {
  date: string;
  total_scans: number;
  uv?: number;
  first_scans?: number;
}

interface ScanTrendChartProps {
  data: TrendRow[];
  height?: number;
  showMulti?: boolean; // 是否显示多指标
}

export default function ScanTrendChart({ data, height = 250, showMulti = false }: ScanTrendChartProps) {
  if (!data || data.length === 0) {
    return null;
  }

  if (showMulti) {
    // 多指标：将数据转为长格式
    const multiData = data.flatMap((row) => [
      { date: row.date, value: row.total_scans, metric: "扫码量" },
      { date: row.date, value: row.uv ?? 0, metric: "UV" },
      { date: row.date, value: row.first_scans ?? 0, metric: "首扫" },
    ]);

    const config = {
      data: multiData,
      xField: "date",
      yField: "value",
      colorField: "metric",
      height,
      smooth: true,
      point: { shapeField: "circle", sizeField: 3 },
      axis: {
        x: { title: "日期" },
        y: { title: "数量" },
      },
    };

    return <Line {...config} />;
  }

  // 单指标：只显示扫码量
  const config = {
    data,
    xField: "date",
    yField: "total_scans",
    height,
    smooth: true,
    style: { lineWidth: 2 },
    axis: {
      x: { title: "日期" },
      y: { title: "扫码量" },
    },
  };

  return <Line {...config} />;
}
```

- [ ] **Step 3: 创建环境占比饼图组件**

```typescript
// frontend/apps/admin/src/components/EnvBreakdownChart.tsx
"use client";

import { Pie } from "@ant-design/charts";

interface EnvBreakdownChartProps {
  data: Record<string, number>;
  height?: number;
}

const ENV_LABELS: Record<string, string> = {
  wechat: "微信",
  alipay: "支付宝",
  browser: "浏览器",
  unknown: "未知",
};

export default function EnvBreakdownChart({ data, height = 250 }: EnvBreakdownChartProps) {
  const chartData = Object.entries(data).map(([key, value]) => ({
    env: ENV_LABELS[key] || key,
    value,
  }));

  if (chartData.length === 0) {
    return null;
  }

  const config = {
    data: chartData,
    angleField: "value",
    colorField: "env",
    height,
    label: {
      text: (d: { env: string; value: number }) => `${d.env}: ${d.value}`,
      position: "outside" as const,
    },
    legend: { color: { position: "bottom" as const } },
  };

  return <Pie {...config} />;
}
```

- [ ] **Step 4: Commit**

```bash
git add frontend/apps/admin/package.json frontend/apps/admin/pnpm-lock.yaml frontend/apps/admin/src/components/ScanTrendChart.tsx frontend/apps/admin/src/components/EnvBreakdownChart.tsx
git commit -m "feat(admin): add @ant-design/charts, ScanTrendChart and EnvBreakdownChart components"
```

---

### Task 7: 工作台集成趋势图、环境占比和导出日期选择

**Why:** 工作台趋势只有表格、环境数据后端返回但前端未展示、导出固定7天不可选。

**Files:**
- Modify: `frontend/apps/admin/src/app/(dashboard)/_components/DashboardHome.tsx`

- [ ] **Step 1: 替换趋势表格为折线图**

在 `DashboardHome.tsx` 中，添加组件导入：

```typescript
import ScanTrendChart from "@/components/ScanTrendChart";
import EnvBreakdownChart from "@/components/EnvBreakdownChart";
```

更新 `DashboardData` 接口，添加 environment_breakdown：

```typescript
interface DashboardData {
  today_scans: number;
  cumulative_scans: number;
  cumulative_first_scans: number;
  first_scan_rate: number;
  environment_breakdown?: Record<string, number>;
  trend?: TrendRow[];
}
```

添加 state 用于导出日期范围：

```typescript
  const [exportRange, setExportRange] = useState<[Dayjs, Dayjs]>([
    dayjs().subtract(6, "day"),
    dayjs(),
  ]);
```

替换"最近 7 天扫码趋势" Card（约第 217-219 行）：

```typescript
        <Col xs={24} lg={12}>
          <Card title="最近 7 天扫码趋势" size="small">
            {loading ? (
              <div style={{ height: 250 }} className="flex items-center justify-center text-gray-400">
                加载中...
              </div>
            ) : (
              <ScanTrendChart data={trend} height={250} />
            )}
          </Card>
        </Col>
```

- [ ] **Step 2: 添加环境占比展示**

在"最近码批次"Card 之后（约第 227 行之后）添加环境占比卡片。将趋势和批次改为占 8 列，右侧新增环境占比：

```typescript
      <Row gutter={[16, 16]} className="mb-6">
        <Col xs={24} lg={8}>
          <Card title="最近 7 天扫码趋势" size="small">
            {loading ? (
              <div style={{ height: 250 }} className="flex items-center justify-center text-gray-400">
                加载中...
              </div>
            ) : (
              <ScanTrendChart data={trend} height={250} />
            )}
          </Card>
        </Col>
        <Col xs={24} lg={8}>
          <Card title="最近码批次" size="small">
            <Table columns={batchColumns} dataSource={batches} rowKey="id" pagination={false} size="small" />
          </Card>
        </Col>
        <Col xs={24} lg={8}>
          <Card title="扫码环境占比" size="small">
            {data?.environment_breakdown && Object.keys(data.environment_breakdown).length > 0 ? (
              <EnvBreakdownChart data={data.environment_breakdown} height={250} />
            ) : (
              <div style={{ height: 250 }} className="flex items-center justify-center text-gray-400">
                暂无环境数据
              </div>
            )}
          </Card>
        </Col>
      </Row>
```

- [ ] **Step 3: 导出按钮增加日期范围选择**

替换导出按钮区域（约第 163-191 行）：

```typescript
        <Space>
          <RangePicker
            value={exportRange}
            onChange={(dates) => {
              if (dates && dates[0] && dates[1]) {
                setExportRange([dates[0], dates[1]]);
              }
            }}
            disabledDate={(current) => current && current.isAfter(dayjs().endOf("day"))}
          />
          <Button
            icon={<DownloadOutlined />}
            onClick={async () => {
              try {
                const end = exportRange[1].format("YYYY-MM-DD");
                const start = exportRange[0].format("YYYY-MM-DD");
                const response = await api.post("/analytics/exports", null, {
                  params: { export_type: "scan_events", start_date: start, end_date: end },
                  responseType: "blob",
                });
                const blob = new Blob([response.data], { type: "text/csv" });
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement("a");
                a.href = url;
                a.download = `scan-events-${start}-${end}.csv`;
                document.body.appendChild(a);
                a.click();
                a.remove();
                window.URL.revokeObjectURL(url);
                message.success("导出成功");
              } catch (err) {
                message.error(extractErrorMessage(err, "导出失败，请确认您有管理员权限"));
              }
            }}
          >
            导出扫码数据
          </Button>
        </Space>
```

需要在文件顶部添加 `RangePicker` 导入（在 DatePicker 中解构）：

```typescript
import { App, Button, Card, Col, DatePicker, Row, Space, Statistic, Table, Tag, Typography } from "antd";
const { RangePicker } = DatePicker;
```

以及 `dayjs` 的类型导入（确认已有）：

```typescript
import dayjs, { type Dayjs } from "dayjs";
```

- [ ] **Step 4: 验证工作台展示**

打开工作台，确认趋势折线图、环境占比饼图、导出日期选择器正常工作。

- [ ] **Step 5: Commit**

```bash
git add frontend/apps/admin/src/app/\(dashboard\)/_components/DashboardHome.tsx
git commit -m "feat(dashboard): add trend line chart, environment pie chart, and export date range picker"
```

---

### Task 8: 扫码统计页和活动看板添加趋势图

**Why:** stats 页面和 campaign-analytics 页面的趋势数据都用纯 Table 渲染。

**Files:**
- Modify: `frontend/apps/admin/src/app/(dashboard)/stats/page.tsx`
- Modify: `frontend/apps/admin/src/app/(dashboard)/campaign-analytics/page.tsx`

- [ ] **Step 1: stats 页面添加趋势图**

在 `stats/page.tsx` 中添加导入：

```typescript
import ScanTrendChart from "@/components/ScanTrendChart";
```

在汇总卡片和 RangePicker 之间（约第 128 行之后）插入趋势图：

```typescript
      {/* 扫码趋势折线图 */}
      {data.length > 0 && (
        <Card title="扫码趋势" size="small" className="mb-6">
          <ScanTrendChart data={data} height={300} showMulti />
        </Card>
      )}
```

- [ ] **Step 2: campaign-analytics 页面添加趋势图**

在 `campaign-analytics/page.tsx` 中添加导入：

```typescript
import ScanTrendChart from "@/components/ScanTrendChart";
```

将"扫码趋势明细"Card（约第 234-243 行）替换为图表+表格折叠：

```typescript
      <Card title="扫码趋势" className="mb-6" size="small">
        {trend.length > 0 && <ScanTrendChart data={trend} height={300} showMulti />}
        <Table
          columns={trendColumns}
          dataSource={trend}
          rowKey="date"
          loading={loading}
          pagination={{ pageSize: 10, showTotal: (t) => `共 ${t} 条` }}
          size="small"
        />
      </Card>
```

- [ ] **Step 3: 验证趋势图**

分别打开扫码统计和活动看板页面，确认折线图正常渲染，鼠标悬浮可看数据。

- [ ] **Step 4: Commit**

```bash
git add frontend/apps/admin/src/app/\(dashboard\)/stats/page.tsx frontend/apps/admin/src/app/\(dashboard\)/campaign-analytics/page.tsx
git commit -m "feat(stats,campaign-analytics): add ScanTrendChart for visual trend display"
```

---

## Wave 4: 活动维度统计

### Task 9: 后端新增活动维度扫码统计 API

**Why:** 当前活动看板的扫码趋势是全局数据，不是活动维度的。需要后端提供按 campaign 过滤的扫码统计。

**Files:**
- Modify: `backend/app/services/analytics.py`
- Modify: `backend/app/api/v1/analytics.py`
- Create: `backend/tests/test_api/test_analytics_api.py`

- [ ] **Step 1: 写活动维度统计 API 测试**

```python
# backend/tests/test_api/test_analytics_api.py
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_scan_stats_default_range(admin_client: AsyncClient):
    """默认返回最近 7 天数据"""
    resp = await admin_client.get("/api/v1/analytics/scan-stats")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)


@pytest.mark.asyncio
async def test_scan_stats_with_date_range(admin_client: AsyncClient):
    """指定日期范围"""
    resp = await admin_client.get(
        "/api/v1/analytics/scan-stats",
        params={"start_date": "2026-01-01", "end_date": "2026-01-31"},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_campaign_scan_stats(admin_client: AsyncClient):
    """活动维度扫码统计"""
    resp = await admin_client.get("/api/v1/analytics/campaign-scan-stats")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && source .venv/bin/activate && python -m pytest tests/test_api/test_analytics_api.py -v`
Expected: `test_campaign_scan_stats` FAIL（端点不存在）

- [ ] **Step 3: 实现活动维度统计服务**

在 `backend/app/services/analytics.py` 中添加：

```python
async def get_campaign_scan_stats(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    campaign_id: uuid.UUID | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[dict]:
    """获取活动维度的扫码统计"""
    if not start_date:
        start_date = date.today() - timedelta(days=30)
    if not end_date:
        end_date = date.today()

    # 构建基础查询：通过 code_batch 关联 campaign
    from app.models.campaign import Campaign
    from app.models.code import CodeBatch, CodeItem

    # 如果指定了 campaign_id，只查询该活动的码批次
    batch_ids_stmt = (
        select(CodeBatch.id)
        .where(CodeBatch.tenant_id == tenant_id)
    )
    if campaign_id:
        batch_ids_stmt = batch_ids_stmt.where(CodeBatch.campaign_id == campaign_id)

    batch_result = await db.execute(batch_ids_stmt)
    batch_ids = [row[0] for row in batch_result.all()]

    if not batch_ids:
        return []

    # 查询这些码批次的每日扫码统计
    # 先查这些批次关联的 public_id 列表（通过 code_items）
    # 然后从 scan_events 按 public_id 过滤
    from datetime import UTC, datetime

    start_dt = datetime(start_date.year, start_date.month, start_date.day, tzinfo=UTC)
    end_dt = datetime(end_date.year, end_date.month, end_date.day, tzinfo=UTC) + timedelta(days=1)

    # 按 public_id 前缀或直接从 code_items 关联
    code_stmt = (
        select(CodeItem.public_id)
        .where(
            CodeItem.tenant_id == tenant_id,
            CodeItem.code_batch_id.in_(batch_ids),
        )
    )
    code_result = await db.execute(code_stmt)
    public_ids = [row[0] for row in code_result.all()]

    if not public_ids:
        return []

    # 按日期分组统计
    result = await db.execute(
        select(
            func.date_trunc("day", ScanEvent.scan_time).label("day"),
            func.count().label("total_scans"),
            func.count(ScanEvent.public_id.distinct()).label("uv"),
            func.sum(ScanEvent.is_first_scan.cast(Integer)).label("first_scans"),
        )
        .where(
            ScanEvent.tenant_id == tenant_id,
            ScanEvent.public_id.in_(public_ids),
            ScanEvent.scan_time >= start_dt,
            ScanEvent.scan_time < end_dt,
        )
        .group_by("day")
        .order_by("day")
    )

    rows = result.all()
    return [
        {
            "date": str(row.day.date()) if row.day else "",
            "total_scans": row.total_scans or 0,
            "uv": row.uv or 0,
            "first_scans": int(row.first_scans or 0),
            "rescans": (row.total_scans or 0) - int(row.first_scans or 0),
        }
        for row in rows
    ]
```

- [ ] **Step 4: 添加 API 端点**

在 `backend/app/api/v1/analytics.py` 中添加：

```python
from app.services.analytics import get_campaign_scan_stats


@analytics_router.get("/campaign-scan-stats", summary="获取活动维度扫码统计")
async def get_campaign_scan_stats_endpoint(
    campaign_id: uuid.UUID | None = Query(None),
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
):
    return await get_campaign_scan_stats(db, tenant_id, campaign_id, start_date, end_date)
```

- [ ] **Step 5: 运行测试确认通过**

Run: `cd backend && source .venv/bin/activate && python -m pytest tests/test_api/test_analytics_api.py -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/analytics.py backend/app/api/v1/analytics.py backend/tests/test_api/test_analytics_api.py
git commit -m "feat(analytics): add campaign-scoped scan stats API endpoint"
```

---

### Task 10: 活动看板对接活动维度统计

**Why:** 前端活动看板需要使用新的 campaign-scan-stats 端点，并添加活动选择器。

**Files:**
- Modify: `frontend/apps/admin/src/app/(dashboard)/campaign-analytics/page.tsx`

- [ ] **Step 1: 添加活动列表 state 和选择器**

在 `campaign-analytics/page.tsx` 中添加 state：

```typescript
  const [campaigns, setCampaigns] = useState<{ id: string; name: string }[]>([]);
  const [selectedCampaign, setSelectedCampaign] = useState<string | undefined>(undefined);
```

添加活动列表获取函数（在 `fetchCodeBatches` 之后）：

```typescript
  const fetchCampaigns = useCallback(async () => {
    try {
      const { data } = await api.get("/campaigns", {
        params: { page: 1, page_size: 100 },
      });
      setCampaigns(
        (data.items || []).map((c: { id: string; name: string }) => ({
          id: c.id,
          name: c.name,
        }))
      );
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    fetchCampaigns();
  }, [fetchCampaigns]);
```

- [ ] **Step 2: 修改趋势数据获取为活动维度**

替换 `fetchTrend` 函数（约第 56-70 行）：

```typescript
  const fetchTrend = useCallback(async () => {
    setLoading(true);
    try {
      const params: Record<string, string> = {
        start_date: dateRange[0].format("YYYY-MM-DD"),
        end_date: dateRange[1].format("YYYY-MM-DD"),
      };
      if (selectedCampaign) {
        params.campaign_id = selectedCampaign;
      }
      const endpoint = selectedCampaign
        ? "/analytics/campaign-scan-stats"
        : "/analytics/scan-stats";
      const { data } = await api.get(endpoint, { params });
      setTrend(Array.isArray(data) ? data : data?.details || []);
    } catch (err) {
      setTrend([]);
    } finally {
      setLoading(false);
    }
  }, [dateRange, selectedCampaign]);
```

- [ ] **Step 3: 在页面中添加活动选择器**

在 RangePicker 旁边（约第 179-193 行的 Space 中）添加活动选择器：

```typescript
      <div className="mb-4">
        <Space wrap>
          <Select
            placeholder="全部活动"
            allowClear
            showSearch
            optionFilterProp="label"
            style={{ width: 200 }}
            value={selectedCampaign}
            onChange={(v) => setSelectedCampaign(v)}
            options={campaigns.map((c) => ({
              value: c.id,
              label: c.name,
            }))}
          />
          <RangePicker
            value={dateRange}
            onChange={(dates) => {
              if (dates && dates[0] && dates[1]) {
                setDateRange([dates[0], dates[1]]);
              }
            }}
          />
          <Button icon={<DownloadOutlined />} onClick={handleExport}>
            导出 Excel
          </Button>
        </Space>
      </div>
```

- [ ] **Step 4: 验证活动维度统计**

打开活动看板，选择一个活动，确认趋势数据按该活动的码范围过滤。

- [ ] **Step 5: Commit**

```bash
git add frontend/apps/admin/src/app/\(dashboard\)/campaign-analytics/page.tsx
git commit -m "feat(campaign-analytics): use campaign-scoped scan stats with campaign selector"
```

---

## 最终验证

### Task 11: 全流程验证与构建检查

- [ ] **Step 1: 后端测试全部通过**

Run: `cd backend && source .venv/bin/activate && python -m pytest tests/test_api/test_analytics_api.py tests/test_api/test_sse_ticket.py -v`
Expected: ALL PASS

- [ ] **Step 2: 前端构建无报错**

Run: `cd /Users/ericding/code/agriculture/yimatong/frontend && pnpm build:admin`
Expected: BUILD SUCCESS

- [ ] **Step 3: 前端 Lint 无错误**

Run: `cd /Users/ericding/code/agriculture/yimatong/frontend && pnpm lint:admin`
Expected: No errors

- [ ] **Step 4: 手动验证关键路径**

逐项验证：
1. 工作台：趋势折线图 ✓ 环境占比饼图 ✓ 导出可选日期 ✓
2. 扫码统计：趋势折线图 ✓ 卡片 loading ✓ 空状态引导 ✓ 日期校验 ✓ 后端导出 ✓ 错误提示 ✓
3. 活动看板：码批次统计正确 ✓ 活动选择器 ✓ 活动维度趋势 ✓
4. 风控看板：SSE ticket 认证 ✓ 实时告警连接 ✓

- [ ] **Step 5: Final Commit**

```bash
git add -A
git commit -m "chore: scan stats UX improvements complete - verified all paths"
```
