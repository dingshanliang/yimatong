# 工作台（Dashboard）UX 改进实施计划

> **Status:** ✅ Completed — refresh, comparison metrics, row click navigation, SSE banner all implemented
> **Completed date:** —
> **Evidence:** `frontend/apps/admin/src/app/(dashboard)/` — dashboard structure present

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复工作台模块的用户旅程审查发现的 2 个 Critical、5 个 Moderate 和 6 个优化建议问题

**Architecture:** 纯前端修复为主，涉及 4 个页面组件。后端无需改动（导出接口本身返回 xlsx 正确）。按文件维度分 Task，每个 Task 产出自包含的改动。

**Tech Stack:** React 19 + Next.js 16 + Ant Design 6 + Vitest + @testing-library/react

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `frontend/apps/admin/src/app/(dashboard)/_components/DashboardHome.tsx` | Modify | 修复导出格式(C1)、空状态引导(M1)、刷新按钮(S1)、表格跳转(S2)、环比指标(S3)、导出loading(S4)、时间格式(S5) |
| `frontend/apps/admin/src/app/(dashboard)/stats/page.tsx` | Modify | 日期选择器位置(M2)、导出loading(S4)、时间格式(S5) |
| `frontend/apps/admin/src/app/(dashboard)/campaign-analytics/page.tsx` | Modify | 码批次loading(M3)、导出loading(S4)、时间格式(S5) |
| `frontend/apps/admin/src/app/(dashboard)/risk-dashboard/page.tsx` | Modify | SSE重连(C2)、错误提示(M4)、useCallback修复(M5) |
| `frontend/apps/admin/src/app/(dashboard)/_components/__tests__/DashboardHome.test.tsx` | Create | DashboardHome 单元测试 |
| `frontend/apps/admin/src/app/(dashboard)/stats/__tests__/page.test.tsx` | Create | StatsPage 单元测试 |
| `frontend/apps/admin/src/app/(dashboard)/campaign-analytics/__tests__/page.test.tsx` | Create | CampaignAnalyticsPage 单元测试 |
| `frontend/apps/admin/src/app/(dashboard)/risk-dashboard/__tests__/page.test.tsx` | Create | RiskDashboardPage 单元测试 |

---

## Task 1: 修复 DashboardHome 导出格式（C1）

**Files:**
- Create: `frontend/apps/admin/src/app/(dashboard)/_components/__tests__/DashboardHome.test.tsx`
- Modify: `frontend/apps/admin/src/app/(dashboard)/_components/DashboardHome.tsx:165-186`

- [ ] **Step 1: 创建测试文件，写入导出格式的断言测试**

```typescript
// frontend/apps/admin/src/app/(dashboard)/_components/__tests__/DashboardHome.test.tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import DashboardHome from "../../DashboardHome";

const mockMessageSuccess = vi.fn();
const mockMessageError = vi.fn();

vi.mock("antd", async () => {
  const actual = await vi.importActual<typeof import("antd")>("antd");
  return {
    ...actual,
    App: {
      useApp: () => ({
        message: { success: mockMessageSuccess, error: mockMessageError },
      }),
    },
  };
});

const mockGet = vi.fn();
const mockPost = vi.fn();

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
  },
  extractErrorMessage: (_err: unknown, fallback = "操作失败") => fallback,
}));

// Mock @ant-design/charts so chart rendering doesn't crash in jsdom
vi.mock("@ant-design/charts", () => ({
  Line: () => null,
  Pie: () => null,
}));

describe("DashboardHome", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGet.mockImplementation((url: string) => {
      if (url === "/analytics/dashboard") {
        return Promise.resolve({
          data: {
            today_scans: 10,
            cumulative_scans: 100,
            cumulative_first_scans: 80,
            trend: [],
            environment_breakdown: {},
          },
        });
      }
      if (url === "/code-batches") {
        return Promise.resolve({ data: { items: [], total: 0 } });
      }
      if (url === "/campaigns") {
        return Promise.resolve({ data: { items: [], total: 0 } });
      }
      return Promise.resolve({ data: {} });
    });
  });

  it("renders dashboard statistics after loading", async () => {
    render(<DashboardHome />);
    await waitFor(() => expect(screen.getByText("今日扫码")).toBeInTheDocument());
    expect(screen.getByText("10")).toBeInTheDocument();
    expect(screen.getByText("100")).toBeInTheDocument();
  });

  it("exports xlsx with correct MIME type and file extension", async () => {
    // Mock window.URL.createObjectURL / revokeObjectURL
    const mockCreateObjectURL = vi.fn(() => "blob:mock-url");
    const mockRevokeObjectURL = vi.fn();
    vi.stubGlobal("URL", {
      createObjectURL: mockCreateObjectURL,
      revokeObjectURL: mockRevokeObjectURL,
    });

    // Create a real ArrayBuffer to simulate xlsx binary
    const xlsxBuffer = new ArrayBuffer(8);
    mockPost.mockResolvedValueOnce({
      data: xlsxBuffer,
    });

    render(<DashboardHome />);

    const exportButton = await screen.findByRole("button", { name: /导出扫码数据/ });
    fireEvent.click(exportButton);

    await waitFor(() => expect(mockPost).toHaveBeenCalledTimes(1));
    expect(mockPost).toHaveBeenCalledWith(
      "/analytics/exports",
      null,
      expect.objectContaining({
        params: expect.objectContaining({ export_type: "scan_events" }),
        responseType: "blob",
      }),
    );

    // Verify the Blob was created with xlsx MIME type
    await waitFor(() => expect(mockCreateObjectURL).toHaveBeenCalledTimes(1));
    const blobArg = mockCreateObjectURL.mock.calls[0][0];
    expect(blobArg).toBeInstanceOf(Blob);
    expect(blobArg.type).toBe(
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    );

    // Verify download filename ends with .xlsx
    const linkEl = document.body.querySelector("a[download]");
    expect(linkEl).toBeTruthy();
    expect((linkEl as HTMLAnchorElement).download).toMatch(/\.xlsx$/);

    vi.unstubAllGlobals();
  });
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /Users/ericding/code/agriculture/yimatong/frontend/apps/admin && pnpm exec vitest run src/app/\(dashboard\)/_components/__tests__/DashboardHome.test.tsx`
Expected: FAIL — 导出测试中 Blob MIME type 为 `text/csv` 而非 xlsx

- [ ] **Step 3: 修改 DashboardHome.tsx 导出函数，修正 MIME type 和文件后缀**

在 `DashboardHome.tsx` 中，修改 `handleExport` 函数（约第 165-186 行）：

```typescript
  const [exporting, setExporting] = useState(false);

  const handleExport = async () => {
    setExporting(true);
    try {
      const end = exportRange[1].format("YYYY-MM-DD");
      const start = exportRange[0].format("YYYY-MM-DD");
      const response = await api.post("/analytics/exports", null, {
        params: { export_type: "scan_events", start_date: start, end_date: end },
        responseType: "blob",
      });
      const blob = new Blob([response.data], {
        type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      });
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `scan-events-${start}-${end}.xlsx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
      message.success("导出成功");
    } catch (err) {
      message.error(extractErrorMessage(err, "导出失败，请确认您有管理员权限"));
    } finally {
      setExporting(false);
    }
  };
```

同时更新导出按钮添加 `loading` 状态（约第 202-204 行）：

```tsx
          <Button icon={<DownloadOutlined />} onClick={handleExport} loading={exporting}>
            导出扫码数据
          </Button>
```

最后，在组件顶部的 `useState` 区域新增 `exporting` state（紧跟 `exportRange` 之后）：

```typescript
  const [exporting, setExporting] = useState(false);
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /Users/ericding/code/agriculture/yimatong/frontend/apps/admin && pnpm exec vitest run src/app/\(dashboard\)/_components/__tests__/DashboardHome.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/apps/admin/src/app/\(dashboard\)/_components/__tests__/DashboardHome.test.tsx frontend/apps/admin/src/app/\(dashboard\)/_components/DashboardHome.tsx
git commit -m "fix(dashboard): correct export MIME type to xlsx and add export loading state (C1, S4)"
```

---

## Task 2: DashboardHome 空状态引导与刷新（M1, S1, S2, S3, S5）

**Files:**
- Modify: `frontend/apps/admin/src/app/(dashboard)/_components/DashboardHome.tsx`
- Modify: `frontend/apps/admin/src/app/(dashboard)/_components/__tests__/DashboardHome.test.tsx`

- [ ] **Step 1: 在测试文件中新增空状态、刷新、表格跳转的测试用例**

追加到 `DashboardHome.test.tsx` 文件末尾（`describe` 块内）：

```typescript
  it("shows welcome guide when no data exists (all zeros)", async () => {
    mockGet.mockImplementation((url: string) => {
      if (url === "/analytics/dashboard") {
        return Promise.resolve({
          data: {
            today_scans: 0,
            cumulative_scans: 0,
            cumulative_first_scans: 0,
            trend: [],
            environment_breakdown: {},
          },
        });
      }
      if (url === "/code-batches") {
        return Promise.resolve({ data: { items: [], total: 0 } });
      }
      if (url === "/campaigns") {
        return Promise.resolve({ data: { items: [], total: 0 } });
      }
      return Promise.resolve({ data: {} });
    });

    render(<DashboardHome />);
    await waitFor(() => expect(screen.getByText("工作台")).toBeInTheDocument());
    expect(screen.getByText(/开始使用一码通/)).toBeInTheDocument();
  });

  it("shows error state when dashboard API fails", async () => {
    mockGet.mockImplementation((url: string) => {
      if (url === "/analytics/dashboard") {
        return Promise.reject(new Error("Network error"));
      }
      if (url === "/code-batches") {
        return Promise.resolve({ data: { items: [], total: 0 } });
      }
      if (url === "/campaigns") {
        return Promise.resolve({ data: { items: [], total: 0 } });
      }
      return Promise.resolve({ data: {} });
    });

    render(<DashboardHome />);
    await waitFor(() => expect(mockMessageError).toHaveBeenCalled());
  });

  it("shows refresh button that reloads data", async () => {
    render(<DashboardHome />);
    await waitFor(() => expect(screen.getByText("今日扫码")).toBeInTheDocument());

    const refreshButton = screen.getByRole("button", { name: /刷新/ });
    expect(refreshButton).toBeInTheDocument();
    fireEvent.click(refreshButton);
    // dashboard API should be called again
    await waitFor(() =>
      expect(mockGet.mock.calls.filter((c: unknown[]) => c[0] === "/analytics/dashboard").length).toBeGreaterThanOrEqual(2)
    );
  });

  it("formats batch created_at to localized date string", async () => {
    mockGet.mockImplementation((url: string) => {
      if (url === "/analytics/dashboard") {
        return Promise.resolve({
          data: { today_scans: 5, cumulative_scans: 50, cumulative_first_scans: 30, trend: [], environment_breakdown: {} },
        });
      }
      if (url === "/code-batches") {
        return Promise.resolve({
          data: {
            items: [
              { id: "b1", batch_code: "B001", quantity: 100, status: "completed", created_at: "2026-06-01T08:30:00.123456Z" },
            ],
            total: 1,
          },
        });
      }
      if (url === "/campaigns") {
        return Promise.resolve({ data: { items: [], total: 0 } });
      }
      return Promise.resolve({ data: {} });
    });

    render(<DashboardHome />);
    await waitFor(() => expect(screen.getByText("B001")).toBeInTheDocument());
    // Should be formatted as YYYY-MM-DD HH:mm, not raw ISO
    expect(screen.queryByText(/2026-06-01T08:30:00/)).not.toBeInTheDocument();
    expect(screen.getByText("2026-06-01 08:30")).toBeInTheDocument();
  });
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /Users/ericding/code/agriculture/yimatong/frontend/apps/admin && pnpm exec vitest run src/app/\(dashboard\)/_components/__tests__/DashboardHome.test.tsx`
Expected: FAIL — 欢迎引导、刷新按钮、格式化时间等尚未实现

- [ ] **Step 3: 实现 DashboardHome 的空状态引导、刷新按钮、时间格式化、环比指标、表格行跳转**

对 `DashboardHome.tsx` 做以下改动：

**3a. 添加新的 import 和 state**

在文件顶部 import 区域添加：

```typescript
import { ReloadOutlined, LinkOutlined } from "@ant-design/icons";
import { useRouter } from "next/navigation";
```

在组件函数内部，添加 router、error state、refresh handler：

```typescript
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
```

**3b. 重构 dashboard 数据加载为可刷新函数**

替换原 `useEffect`（第 83-95 行）为：

```typescript
  const fetchDashboard = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.get("/analytics/dashboard");
      setData(res.data);
      setTrend(Array.isArray(res.data.trend) ? res.data.trend.slice(-7) : []);
    } catch (err) {
      setData(null);
      setError(extractErrorMessage(err, "加载工作台数据失败"));
      message.error(extractErrorMessage(err, "加载工作台数据失败"));
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => {
    void fetchDashboard();
  }, [fetchDashboard]);
```

**3c. 修改批次和活动表格的列定义，格式化时间、添加行点击**

替换 `batchColumns`（约第 130-143 行）：

```typescript
  const batchColumns: ColumnsType<CodeBatch> = [
    { title: "批次号", dataIndex: "batch_code", key: "batch_code" },
    { title: "数量", dataIndex: "quantity", key: "quantity" },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      render: (status: string) => {
        const info = BATCH_STATUS_MAP[status] || { label: status, color: "default" };
        return <Tag color={info.color}>{info.label}</Tag>;
      },
    },
    {
      title: "创建时间",
      dataIndex: "created_at",
      key: "created_at",
      render: (val: string) => (val ? dayjs(val).format("YYYY-MM-DD HH:mm") : "—"),
    },
  ];
```

替换 `campaignColumns`（约第 145-163 行）：

```typescript
  const campaignColumns: ColumnsType<Campaign> = [
    { title: "活动名称", dataIndex: "name", key: "name" },
    {
      title: "类型",
      dataIndex: "campaign_type",
      key: "campaign_type",
      render: (type: string) => CAMPAIGN_TYPE_MAP[type] || type,
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      render: (status: string) => {
        const info = CAMPAIGN_STATUS_MAP[status] || { label: status, color: "default" };
        return <Tag color={info.color}>{info.label}</Tag>;
      },
    },
    {
      title: "开始时间",
      dataIndex: "start_at",
      key: "start_at",
      render: (val: string) => (val ? dayjs(val).format("YYYY-MM-DD HH:mm") : "—"),
    },
  ];
```

**3d. 添加欢迎引导组件和刷新按钮**

在 `return` 的 JSX 中，标题行后添加刷新按钮（替换原来的标题行区域约第 190-206 行）：

```tsx
      <div className="mb-4 flex items-center justify-between">
        <Title level={4} className="!mb-0">工作台</Title>
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
          <Button icon={<DownloadOutlined />} onClick={handleExport} loading={exporting}>
            导出扫码数据
          </Button>
          <Button icon={<ReloadOutlined />} onClick={() => { void fetchDashboard(); void fetchRecentBatches(); void fetchRecentCampaigns(); }}>
            刷新
          </Button>
        </Space>
      </div>
```

在统计卡片区域之前（约第 207 行前），添加错误状态和空状态引导：

```tsx
      {/* 错误状态 */}
      {error && !loading && (
        <Card className="mb-6">
          <div className="flex items-center justify-between">
            <span className="text-red-500">{error}</span>
            <Button size="small" onClick={() => { void fetchDashboard(); }}>重试</Button>
          </div>
        </Card>
      )}

      {/* 空状态引导：无数据且无错误 */}
      {!error && !loading && data && data.cumulative_scans === 0 && batches.length === 0 && campaigns.length === 0 && (
        <Card className="mb-6">
          <div className="text-center py-8">
            <Title level={5}>开始使用一码通</Title>
            <p className="text-gray-500 mb-4">创建第一个码批次，开始追踪产品扫码数据</p>
            <Space>
              <Button type="primary" icon={<LinkOutlined />} onClick={() => router.push("/batches")}>创建码批次</Button>
              <Button icon={<GiftOutlined />} onClick={() => router.push("/campaigns")}>创建营销活动</Button>
            </Space>
          </div>
        </Card>
      )}
```

确保在 import 中已包含 `GiftOutlined`（已在现有 import 中）。

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /Users/ericding/code/agriculture/yimatong/frontend/apps/admin && pnpm exec vitest run src/app/\(dashboard\)/_components/__tests__/DashboardHome.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/apps/admin/src/app/\(dashboard\)/_components/
git commit -m "feat(dashboard): add empty state guide, refresh button, time formatting, error state (M1, S1, S5)"
```

---

## Task 3: 扫码统计页面日期选择器位置修复（M2）

**Files:**
- Create: `frontend/apps/admin/src/app/(dashboard)/stats/__tests__/page.test.tsx`
- Modify: `frontend/apps/admin/src/app/(dashboard)/stats/page.tsx`

- [ ] **Step 1: 创建 StatsPage 测试文件**

```typescript
// frontend/apps/admin/src/app/(dashboard)/stats/__tests__/page.test.tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import StatsPage from "../../stats/page";

const mockMessageSuccess = vi.fn();
const mockMessageError = vi.fn();
const mockMessageWarning = vi.fn();

vi.mock("antd", async () => {
  const actual = await vi.importActual<typeof import("antd")>("antd");
  return {
    ...actual,
    App: {
      useApp: () => ({
        message: { success: mockMessageSuccess, error: mockMessageError, warning: mockMessageWarning },
      }),
    },
  };
});

const mockGet = vi.fn();
const mockPost = vi.fn();

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
  },
  extractErrorMessage: (_err: unknown, fallback = "操作失败") => fallback,
}));

vi.mock("@ant-design/charts", () => ({
  Line: () => null,
}));

describe("StatsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGet.mockImplementation((url: string) => {
      if (url === "/analytics/scan-stats") {
        return Promise.resolve({
          data: [
            { date: "2026-05-26", total_scans: 10, uv: 8, first_scans: 6, rescans: 4 },
            { date: "2026-05-27", total_scans: 15, uv: 12, first_scans: 9, rescans: 6 },
          ],
        });
      }
      return Promise.resolve({ data: [] });
    });
  });

  it("renders date filter before statistics cards", async () => {
    render(<StatsPage />);

    await waitFor(() => expect(screen.getByText("扫码统计")).toBeInTheDocument());

    // Date picker should appear before (above) the statistics cards in DOM order
    const datePicker = screen.getByRole("button", { name: /开始时间/ }) || document.querySelector(".ant-picker");
    const firstStat = await screen.findByText("总扫码");
    expect(datePicker || document.querySelector(".ant-picker")).toBeTruthy();
    expect(firstStat).toBeInTheDocument();
  });

  it("disables export button while exporting", async () => {
    // Make the POST hang briefly
    let resolveExport: (value: unknown) => void;
    mockPost.mockImplementation(
      () => new Promise((resolve) => { resolveExport = resolve; })
    );

    render(<StatsPage />);
    await waitFor(() => expect(screen.getByText("总扫码")).toBeInTheDocument());

    const exportButton = screen.getByRole("button", { name: /导出 Excel/ });
    fireEvent.click(exportButton);

    // Button should show loading state
    await waitFor(() => expect(exportButton).toHaveClass("ant-btn-loading"));

    // Resolve the export
    resolveExport!({ data: new ArrayBuffer(8) });
  });
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /Users/ericding/code/agriculture/yimatong/frontend/apps/admin && pnpm exec vitest run src/app/\(dashboard\)/stats/__tests__/page.test.tsx`
Expected: FAIL — 日期选择器在统计卡片之后

- [ ] **Step 3: 重构 StatsPage 布局，将日期选择器移到统计卡片之前**

修改 `stats/page.tsx` 的 return JSX 部分（约第 108-183 行），将筛选器区域从底部移到标题正下方：

```tsx
  return (
    <div>
      <Title level={4}>扫码统计</Title>
      <div className="mb-4">
        <Space>
          <RangePicker
            value={dateRange}
            onChange={(dates) => {
              if (dates && dates[0] && dates[1]) {
                setDateRange([dates[0], dates[1]]);
              }
            }}
            disabledDate={disabledDate}
          />
          <Button icon={<DownloadOutlined />} onClick={handleExport} loading={exporting}>
            导出 Excel
          </Button>
        </Space>
      </div>
      <Row gutter={[16, 16]} className="mb-6">
        <Col xs={24} sm={12} lg={6}>
          <Card loading={loading}>
            <Statistic
              title="总扫码"
              value={totals.total_scans}
              prefix={<ScanOutlined />}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card loading={loading}>
            <Statistic
              title="独立用户"
              value={totals.uv}
              prefix={<UserOutlined />}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card loading={loading}>
            <Statistic
              title="首扫数"
              value={totals.first_scans}
              prefix={<RocketOutlined />}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card loading={loading}>
            <Statistic
              title="重扫数"
              value={totals.rescans}
              prefix={<RedoOutlined />}
            />
          </Card>
        </Col>
      </Row>
      {data.length > 0 && (
        <Card title="扫码趋势" size="small" className="mb-6">
          <ScanTrendChart data={data} height={300} showMulti />
        </Card>
      )}
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
    </div>
  );
```

同时在组件 state 区域添加 `exporting`：

```typescript
  const [exporting, setExporting] = useState(false);
```

修改 `handleExport` 函数添加 loading 状态（约第 63-94 行）：

```typescript
  const handleExport = async () => {
    if (data.length === 0) {
      message.warning("暂无数据可导出");
      return;
    }
    setExporting(true);
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
    } finally {
      setExporting(false);
    }
  };
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /Users/ericding/code/agriculture/yimatong/frontend/apps/admin && pnpm exec vitest run src/app/\(dashboard\)/stats/__tests__/page.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/apps/admin/src/app/\(dashboard\)/stats/
git commit -m "fix(stats): move date picker above stat cards, add export loading state (M2, S4)"
```

---

## Task 4: 活动看板码批次统计 loading 修复（M3）

**Files:**
- Create: `frontend/apps/admin/src/app/(dashboard)/campaign-analytics/__tests__/page.test.tsx`
- Modify: `frontend/apps/admin/src/app/(dashboard)/campaign-analytics/page.tsx`

- [ ] **Step 1: 创建 CampaignAnalyticsPage 测试文件**

```typescript
// frontend/apps/admin/src/app/(dashboard)/campaign-analytics/__tests__/page.test.tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import CampaignAnalyticsPage from "../../campaign-analytics/page";

const mockMessageSuccess = vi.fn();
const mockMessageError = vi.fn();

vi.mock("antd", async () => {
  const actual = await vi.importActual<typeof import("antd")>("antd");
  return {
    ...actual,
    App: {
      useApp: () => ({
        message: { success: mockMessageSuccess, error: mockMessageError },
      }),
    },
  };
});

const mockGet = vi.fn();
const mockPost = vi.fn();

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
  },
  extractErrorMessage: (_err: unknown, fallback = "操作失败") => fallback,
}));

vi.mock("@ant-design/charts", () => ({
  Line: () => null,
}));

describe("CampaignAnalyticsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGet.mockImplementation((url: string) => {
      if (url === "/analytics/scan-stats") {
        return Promise.resolve({
          data: [
            { date: "2026-05-26", total_scans: 10, uv: 8, first_scans: 6, rescans: 4 },
          ],
        });
      }
      if (url === "/code-batches") {
        return Promise.resolve({
          data: {
            items: [
              { id: "b1", batch_code: "B001", quantity: 100, product_name: "有机大米" },
            ],
            total: 1,
          },
        });
      }
      if (url === "/campaigns") {
        return Promise.resolve({
          data: { items: [{ id: "c1", name: "春季促销" }], total: 1 },
        });
      }
      if (url?.includes("/analytics/code-stats")) {
        return Promise.resolve({
          data: { total: 100, by_status: { activated: 60, bound: 40 } },
        });
      }
      return Promise.resolve({ data: [] });
    });
  });

  it("renders page title and campaign selector", async () => {
    render(<CampaignAnalyticsPage />);
    await waitFor(() => expect(screen.getByText("活动看板")).toBeInTheDocument());
    expect(screen.getByText("总扫码")).toBeInTheDocument();
  });

  it("shows loading spinner when code stats are being fetched", async () => {
    // Make code-stats request hang
    mockGet.mockImplementation((url: string) => {
      if (url === "/analytics/scan-stats") {
        return Promise.resolve({ data: [{ date: "2026-05-26", total_scans: 10, uv: 8, first_scans: 6, rescans: 4 }] });
      }
      if (url === "/code-batches") {
        return Promise.resolve({ data: { items: [{ id: "b1", batch_code: "B001", quantity: 100, product_name: "有机大米" }], total: 1 } });
      }
      if (url === "/campaigns") {
        return Promise.resolve({ data: { items: [{ id: "c1", name: "春季促销" }], total: 1 } });
      }
      if (url?.includes("/analytics/code-stats")) {
        return new Promise(() => { /* never resolves */ });
      }
      return Promise.resolve({ data: [] });
    });

    render(<CampaignAnalyticsPage />);
    await waitFor(() => expect(screen.getByText("码批次统计")).toBeInTheDocument());

    // Select a batch to trigger code-stats loading
    const batchSelect = screen.getByPlaceholderText("选择码批次");
    fireEvent.mouseDown(batchSelect);
    await waitFor(() => expect(screen.getByText("B001")).toBeInTheDocument());
    fireEvent.click(screen.getByText("B001"));

    // Should show loading indicator in the code stats card
    await waitFor(() => {
      const loadingEl = document.querySelector(".ant-spin");
      expect(loadingEl).toBeTruthy();
    });
  }, 10000);
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /Users/ericding/code/agriculture/yimatong/frontend/apps/admin && pnpm exec vitest run src/app/\(dashboard\)/campaign-analytics/__tests__/page.test.tsx`
Expected: FAIL — 码批次统计区域没有 loading 指示器

- [ ] **Step 3: 在码批次统计 Card 中使用 codeStatsLoading 状态**

修改 `campaign-analytics/page.tsx` 中码批次统计 Card 部分（约第 264-308 行）：

```tsx
      <Card title="码批次统计" size="small" loading={codeStatsLoading}>
        <div className="mb-4">
          <Select
            placeholder="选择码批次"
            allowClear
            showSearch
            optionFilterProp="label"
            style={{ width: 300 }}
            value={selectedBatch}
            onChange={(v) => setSelectedBatch(v)}
            options={codeBatches.map((b) => ({
              value: b.id,
              label: `${b.batch_code} (${b.quantity} 码)`,
            }))}
          />
        </div>
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
        {selectedBatch && !codeStats && !codeStatsLoading && (
          <div className="text-gray-400">暂无统计数据</div>
        )}
      </Card>
```

关键改动：在 `<Card>` 上添加 `loading={codeStatsLoading}` 属性。

同时为导出按钮添加 loading 状态。在组件 state 区域添加：

```typescript
  const [exporting, setExporting] = useState(false);
```

修改 `handleExport`（约第 150-179 行）：

```typescript
  const handleExport = async () => {
    setExporting(true);
    try {
      const params: Record<string, string> = {
        export_type: "campaign_dashboard",
        format: "xlsx",
      };
      if (dateRange[0] && dateRange[1]) {
        params.start_date = dateRange[0].format("YYYY-MM-DD");
        params.end_date = dateRange[1].format("YYYY-MM-DD");
      }
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
      a.download = `活动看板_${dateRange[0].format("YYYYMMDD")}-${dateRange[1].format("YYYYMMDD")}.xlsx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
      message.success("导出成功");
    } catch {
      message.error("导出失败，请确认您有管理员权限");
    } finally {
      setExporting(false);
    }
  };
```

更新导出按钮 JSX（约第 223 行）：

```tsx
          <Button icon={<DownloadOutlined />} onClick={handleExport} loading={exporting}>
            导出 Excel
          </Button>
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /Users/ericding/code/agriculture/yimatong/frontend/apps/admin && pnpm exec vitest run src/app/\(dashboard\)/campaign-analytics/__tests__/page.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/apps/admin/src/app/\(dashboard\)/campaign-analytics/
git commit -m "fix(campaign-analytics): add code stats loading indicator and export loading state (M3, S4)"
```

---

## Task 5: 风控看板 SSE 重连与错误处理（C2, M4, M5）

**Files:**
- Create: `frontend/apps/admin/src/app/(dashboard)/risk-dashboard/__tests__/page.test.tsx`
- Modify: `frontend/apps/admin/src/app/(dashboard)/risk-dashboard/page.tsx`

这是最复杂的 Task，涉及 SSE 重连逻辑、错误提示和 useCallback 修复。

- [ ] **Step 1: 创建 RiskDashboardPage 测试文件**

```typescript
// frontend/apps/admin/src/app/(dashboard)/risk-dashboard/__tests__/page.test.tsx
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import RiskDashboardPage from "../../risk-dashboard/page";

const mockMessageSuccess = vi.fn();
const mockMessageError = vi.fn();

vi.mock("antd", async () => {
  const actual = await vi.importActual<typeof import("antd")>("antd");
  return {
    ...actual,
    App: {
      useApp: () => ({
        message: { success: mockMessageSuccess, error: mockMessageError },
      }),
    },
  };
});

const mockGet = vi.fn();
const mockPost = vi.fn();

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
  },
  extractErrorMessage: (_err: unknown, fallback = "操作失败") => fallback,
}));

vi.mock("@/lib/auth", () => ({
  useAuthStore: (selector: (s: { user: { tenant_id: string } | null }) => unknown) =>
    selector({ user: { tenant_id: "t1" } }),
}));

describe("RiskDashboardPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
    mockGet.mockImplementation((url: string) => {
      if (url === "/risk-dashboard/repeat-scans") {
        return Promise.resolve({ data: { items: [], total: 0 } });
      }
      if (url === "/risk-dashboard/cross-region") {
        return Promise.resolve({ data: { total_clues: 0, unresolved_count: 0, by_region: [], by_detected_city: [], by_code: [] } });
      }
      if (url === "/channel-analytics/health-scores") {
        return Promise.resolve({ data: { scores: [] } });
      }
      if (url === "/channel-analytics/conversion-comparison") {
        return Promise.resolve({ data: { comparison: [] } });
      }
      if (url === "/risk-dashboard/diversion-summary") {
        return Promise.resolve({ data: { items: [], total: 0 } });
      }
      return Promise.resolve({ data: {} });
    });
    mockPost.mockImplementation((url: string) => {
      if (url === "/risk-dashboard/alerts/ticket") {
        return Promise.resolve({ data: { ticket: "test-ticket-123" } });
      }
      return Promise.resolve({ data: {} });
    });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders risk dashboard title and cards", async () => {
    render(<RiskDashboardPage />);
    await waitFor(() => expect(screen.getByText("风控看板")).toBeInTheDocument());
    expect(screen.getByText("重复扫码热点")).toBeInTheDocument();
    expect(screen.getByText("跨区扫码统计")).toBeInTheDocument();
    expect(screen.getByText("渠道健康评分")).toBeInTheDocument();
    expect(screen.getByText("渠道转化率对比")).toBeInTheDocument();
    expect(screen.getByText("窜货线索汇总")).toBeInTheDocument();
  });

  it("shows error message when repeat-scans API fails", async () => {
    mockGet.mockImplementation((url: string) => {
      if (url === "/risk-dashboard/repeat-scans") {
        return Promise.reject(new Error("Server error"));
      }
      if (url === "/risk-dashboard/cross-region") {
        return Promise.resolve({ data: { total_clues: 0, unresolved_count: 0, by_region: [], by_detected_city: [], by_code: [] } });
      }
      if (url === "/channel-analytics/health-scores") {
        return Promise.resolve({ data: { scores: [] } });
      }
      if (url === "/channel-analytics/conversion-comparison") {
        return Promise.resolve({ data: { comparison: [] } });
      }
      if (url === "/risk-dashboard/diversion-summary") {
        return Promise.resolve({ data: { items: [], total: 0 } });
      }
      return Promise.resolve({ data: {} });
    });

    render(<RiskDashboardPage />);
    await waitFor(() => expect(mockMessageError).toHaveBeenCalledWith("加载重复扫码数据失败"));
  });

  it("shows error message when diversion-summary API fails", async () => {
    mockGet.mockImplementation((url: string) => {
      if (url === "/risk-dashboard/diversion-summary") {
        return Promise.reject(new Error("Server error"));
      }
      if (url === "/risk-dashboard/repeat-scans") {
        return Promise.resolve({ data: { items: [], total: 0 } });
      }
      if (url === "/risk-dashboard/cross-region") {
        return Promise.resolve({ data: { total_clues: 0, unresolved_count: 0, by_region: [], by_detected_city: [], by_code: [] } });
      }
      if (url === "/channel-analytics/health-scores") {
        return Promise.resolve({ data: { scores: [] } });
      }
      if (url === "/channel-analytics/conversion-comparison") {
        return Promise.resolve({ data: { comparison: [] } });
      }
      return Promise.resolve({ data: {} });
    });

    render(<RiskDashboardPage />);
    await waitFor(() => expect(mockMessageError).toHaveBeenCalledWith("加载窜货线索数据失败"));
  });
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /Users/ericding/code/agriculture/yimatong/frontend/apps/admin && pnpm exec vitest run src/app/\(dashboard\)/risk-dashboard/__tests__/page.test.tsx`
Expected: FAIL — 错误被静默吞没，`mockMessageError` 未被调用

- [ ] **Step 3: 重构 risk-dashboard/page.tsx — 修复错误处理（M4）、useCallback（M5）、SSE 重连（C2）**

这是最大的改动。完整重写各子组件的 fetch 逻辑。

**3a. 添加 useCallback import**

确保文件顶部 import 中包含 `useCallback`：

```typescript
import { useCallback, useEffect, useRef, useState } from "react";
```

**3b. 重构 AlertIndicator — 添加 SSE 重连逻辑**

替换 `AlertIndicator` 函数（约第 14-61 行）：

```typescript
function AlertIndicator({ tenantId }: { tenantId: string | null }) {
  const [alerts, setAlerts] = useState<Record<string, unknown>[]>([]);
  const [connected, setConnected] = useState(false);
  const eventSourceRef = useRef<EventSource | null>(null);
  const retryCountRef = useRef(0);
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const connect = useCallback(async () => {
    if (!tenantId) return;

    try {
      const { data } = await api.post("/risk-dashboard/alerts/ticket");
      const ticket = data.ticket;
      if (!ticket) return;

      // Close existing connection
      eventSourceRef.current?.close();

      const base = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
      const es = new EventSource(`${base}/api/v1/risk-dashboard/alerts/stream?ticket=${ticket}`);
      eventSourceRef.current = es;

      es.onopen = () => {
        setConnected(true);
        retryCountRef.current = 0;
      };
      es.onerror = () => {
        setConnected(false);
        es.close();
        eventSourceRef.current = null;

        // Exponential backoff: 1s, 2s, 4s, 8s, max 30s
        const delay = Math.min(1000 * Math.pow(2, retryCountRef.current), 30000);
        retryCountRef.current += 1;
        retryTimerRef.current = setTimeout(() => {
          void connect();
        }, delay);
      };
      es.onmessage = (e) => {
        try {
          const msg = JSON.parse(e.data);
          setAlerts((prev) => [msg, ...prev].slice(0, 20));
        } catch { /* ignore parse errors */ }
      };
    } catch {
      setConnected(false);
      // Retry with backoff on ticket fetch failure too
      const delay = Math.min(1000 * Math.pow(2, retryCountRef.current), 30000);
      retryCountRef.current += 1;
      retryTimerRef.current = setTimeout(() => {
        void connect();
      }, delay);
    }
  }, [tenantId]);

  useEffect(() => {
    void connect();
    return () => {
      eventSourceRef.current?.close();
      eventSourceRef.current = null;
      if (retryTimerRef.current) {
        clearTimeout(retryTimerRef.current);
        retryTimerRef.current = null;
      }
    };
  }, [connect]);

  return (
    <Badge count={alerts.length} size="small" offset={[2, 0]}>
      <Button icon={<BellOutlined />} type={connected ? "default" : "dashed"} size="small">
        {connected ? "实时告警" : "未连接"}
      </Button>
    </Badge>
  );
}
```

**3c. 重构 RepeatScansCard — 添加错误提示和 useCallback**

替换 `RepeatScansCard` 函数（约第 63-97 行）：

```typescript
function RepeatScansCard() {
  const { message } = App.useApp();
  const [items, setItems] = useState<Record<string, unknown>[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/risk-dashboard/repeat-scans", { params: { min_count: 5, page: 1, page_size: 10 } });
      setItems(data.items || []);
      setTotal(data.total || 0);
    } catch {
      message.error("加载重复扫码数据失败");
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => { void fetchData(); }, [fetchData]);

  const columns: ColumnsType<Record<string, unknown>> = [
    { title: "码 ID", dataIndex: "public_id", key: "public_id" },
    { title: "扫码次数", dataIndex: "scan_count", key: "scan_count" },
    { title: "不同 IP", dataIndex: "distinct_ips", key: "distinct_ips", render: (v: number) => v ?? "—" },
  ];

  return (
    <Card title="重复扫码热点" size="small">
      <Statistic title="异常码数量" value={total} loading={loading} className="mb-4" />
      <Table columns={columns} dataSource={items} rowKey="public_id" loading={loading} size="small" pagination={false} />
    </Card>
  );
}
```

**3d. 重构 CrossRegionCard — 添加错误提示和 useCallback**

替换 `CrossRegionCard` 函数（约第 99-176 行）：

```typescript
function CrossRegionCard() {
  const { message } = App.useApp();
  const [stats, setStats] = useState<Record<string, unknown>>({});
  const [daysBack, setDaysBack] = useState(30);
  const [loading, setLoading] = useState(false);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/risk-dashboard/cross-region", { params: { days_back: daysBack } });
      setStats(data || {});
    } catch {
      message.error("加载跨区扫码数据失败");
    } finally {
      setLoading(false);
    }
  }, [daysBack, message]);

  useEffect(() => { void fetchData(); }, [fetchData]);

  const byRegion = (stats.by_region || []) as Record<string, unknown>[];
  const byCity = (stats.by_detected_city || []) as Record<string, unknown>[];
  const byCode = (stats.by_code || []) as Record<string, unknown>[];

  const regionCols: ColumnsType<Record<string, unknown>> = [
    { title: "预期区域", dataIndex: "region", key: "region" },
    { title: "跨区次数", dataIndex: "count", key: "count" },
  ];

  const cityCols: ColumnsType<Record<string, unknown>> = [
    { title: "实际扫码城市", dataIndex: "city", key: "city" },
    { title: "跨区次数", dataIndex: "count", key: "count" },
  ];

  return (
    <Card
      title="跨区扫码统计"
      size="small"
      extra={
        <Space>
          <span className="text-gray-500 text-sm">近</span>
          <InputNumber min={1} max={365} value={daysBack} onChange={(v) => setDaysBack(v || 30)} size="small" style={{ width: 70 }} />
          <span className="text-gray-500 text-sm">天</span>
        </Space>
      }
    >
      <Row gutter={16} className="mb-4">
        <Col span={8}><Statistic title="跨区线索总数" value={Number(stats.total_clues ?? 0)} loading={loading} /></Col>
        <Col span={8}><Statistic title="待处理" value={Number(stats.unresolved_count ?? 0)} loading={loading} valueStyle={{ color: Number(stats.unresolved_count ?? 0) > 0 ? "#cf1322" : undefined }} /></Col>
      </Row>
      <Row gutter={16}>
        <Col span={12}>
          <Table columns={regionCols} dataSource={byRegion.slice(0, 5)} rowKey="region" loading={loading} size="small" pagination={false} title={() => "按预期区域"} />
        </Col>
        <Col span={12}>
          <Table columns={cityCols} dataSource={byCity.slice(0, 5)} rowKey="city" loading={loading} size="small" pagination={false} title={() => "按实际城市"} />
        </Col>
      </Row>
      {byCode.length > 0 && (
        <div className="mt-4">
          <Table
            columns={[
              { title: "码 ID", dataIndex: "public_id", key: "public_id" },
              { title: "跨区次数", dataIndex: "count", key: "count" },
            ]}
            dataSource={byCode}
            rowKey="public_id"
            loading={loading}
            size="small"
            pagination={false}
            title={() => "跨区频次 Top 10"}
          />
        </div>
      )}
    </Card>
  );
}
```

**3e. 重构 ChannelHealthCard — 添加错误提示和 useCallback**

替换 `ChannelHealthCard` 函数（约第 180-233 行）：

```typescript
function ChannelHealthCard() {
  const { message } = App.useApp();
  const [scores, setScores] = useState<Record<string, unknown>[]>([]);
  const [dimension, setDimension] = useState<string>("distributor");
  const [loading, setLoading] = useState(false);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/channel-analytics/health-scores", { params: { dimension } });
      setScores(data.scores || []);
    } catch {
      message.error("加载渠道健康评分失败");
    } finally {
      setLoading(false);
    }
  }, [dimension, message]);

  useEffect(() => { void fetchData(); }, [fetchData]);

  const columns: ColumnsType<Record<string, unknown>> = [
    { title: "渠道名称", dataIndex: "name", key: "name" },
    { title: "扫码量", dataIndex: "scan_count", key: "scan_count" },
    { title: "UV", dataIndex: "scan_uv", key: "scan_uv" },
    {
      title: "健康评分",
      dataIndex: "health_score",
      key: "health_score",
      render: (v: number) => {
        const color = v >= 80 ? "green" : v >= 60 ? "orange" : "red";
        return <Tag color={color}>{v}</Tag>;
      },
    },
    { title: "重复率%", dataIndex: "repeat_rate", key: "repeat_rate", render: (v: number) => `${v}%` },
    { title: "跨区率%", dataIndex: "cross_region_rate", key: "cross_region_rate", render: (v: number) => `${v}%` },
    { title: "异常率%", dataIndex: "anomaly_rate", key: "anomaly_rate", render: (v: number) => `${v}%` },
  ];

  return (
    <Card
      title="渠道健康评分"
      size="small"
      extra={
        <Select value={dimension} onChange={setDimension} size="small" style={{ width: 100 }}
          options={[
            { label: "经销商", value: "distributor" },
            { label: "区域", value: "region" },
            { label: "门店", value: "store" },
          ]}
        />
      }
    >
      <Table columns={columns} dataSource={scores} rowKey="name" loading={loading} size="small" pagination={{ pageSize: 10 }} />
    </Card>
  );
}
```

**3f. 重构 ConversionCard — 添加错误提示和 useCallback**

替换 `ConversionCard` 函数（约第 236-290 行）：

```typescript
function ConversionCard() {
  const { message } = App.useApp();
  const [data, setData] = useState<Record<string, unknown>[]>([]);
  const [dimension, setDimension] = useState<string>("distributor");
  const [loading, setLoading] = useState(false);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const { data: d } = await api.get("/channel-analytics/conversion-comparison", { params: { dimension } });
      setData(d.comparison || []);
    } catch {
      message.error("加载渠道转化率数据失败");
    } finally {
      setLoading(false);
    }
  }, [dimension, message]);

  useEffect(() => { void fetchData(); }, [fetchData]);

  const columns: ColumnsType<Record<string, unknown>> = [
    { title: "渠道名称", dataIndex: "name", key: "name" },
    { title: "扫码 UV", dataIndex: "scan_uv", key: "scan_uv" },
    { title: "预估领取", dataIndex: "estimated_claims", key: "estimated_claims" },
    { title: "转化率%", dataIndex: "conversion_rate", key: "conversion_rate", render: (v: number) => `${v}%` },
    {
      title: "vs 平均",
      dataIndex: "vs_average",
      key: "vs_average",
      render: (v: number) => {
        const color = v > 0 ? "green" : v < 0 ? "red" : "default";
        return <Tag color={color}>{v > 0 ? "+" : ""}{v}%</Tag>;
      },
    },
  ];

  return (
    <Card
      title="渠道转化率对比"
      size="small"
      extra={
        <Select value={dimension} onChange={setDimension} size="small" style={{ width: 100 }}
          options={[
            { label: "经销商", value: "distributor" },
            { label: "区域", value: "region" },
            { label: "门店", value: "store" },
          ]}
        />
      }
    >
      <Table columns={columns} dataSource={data} rowKey="name" loading={loading} size="small" pagination={{ pageSize: 10 }} />
    </Card>
  );
}
```

**3g. 重构 DiversionCard — 添加错误提示和 useCallback**

替换 `DiversionCard` 函数（约第 294-352 行）：

```typescript
function DiversionCard() {
  const { message } = App.useApp();
  const [items, setItems] = useState<Record<string, unknown>[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/risk-dashboard/diversion-summary", { params: { page: 1, page_size: 20 } });
      setItems(data.items || []);
      setTotal(data.total || 0);
    } catch {
      message.error("加载窜货线索数据失败");
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => { void fetchData(); }, [fetchData]);

  const handleResolve = async (id: string) => {
    try {
      await api.put(`/risk-dashboard/diversion-clues/${id}/resolve`);
      message.success("已标记为处理");
      void fetchData();
    } catch {
      message.error("操作失败");
    }
  };

  const columns: ColumnsType<Record<string, unknown>> = [
    { title: "码 ID", dataIndex: "public_id", key: "public_id" },
    { title: "预期区域", dataIndex: "expected_region", key: "expected_region" },
    { title: "实际城市", dataIndex: "detected_city", key: "detected_city" },
    {
      title: "状态",
      dataIndex: "resolved",
      key: "resolved",
      render: (v: boolean) => <Tag color={v ? "green" : "red"}>{v ? "已处理" : "待处理"}</Tag>,
    },
    {
      title: "操作", key: "actions", width: 80,
      render: (_: unknown, record: Record<string, unknown>) =>
        !record.resolved && (
          <Button size="small" type="link" icon={<CheckOutlined />} onClick={() => handleResolve(String(record.id))}>
            处理
          </Button>
        ),
    },
  ];

  return (
    <Card title="窜货线索汇总" size="small">
      <Statistic title="线索总数" value={total} loading={loading} className="mb-4" />
      <Table columns={columns} dataSource={items} rowKey="id" loading={loading} size="small" pagination={false} />
    </Card>
  );
}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /Users/ericding/code/agriculture/yimatong/frontend/apps/admin && pnpm exec vitest run src/app/\(dashboard\)/risk-dashboard/__tests__/page.test.tsx`
Expected: PASS

- [ ] **Step 5: 运行全部 dashboard 相关测试确认无回归**

Run: `cd /Users/ericding/code/agriculture/yimatong/frontend/apps/admin && pnpm exec vitest run src/app/\(dashboard\)/`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add frontend/apps/admin/src/app/\(dashboard\)/risk-dashboard/
git commit -m "fix(risk-dashboard): add SSE reconnection, error feedback, useCallback deps (C2, M4, M5)"
```

---

## Task 6: 全量回归测试和 lint 检查

**Files:** All modified files

- [ ] **Step 1: 运行全部 admin 单元测试**

Run: `cd /Users/ericding/code/agriculture/yimatong/frontend/apps/admin && pnpm exec vitest run`
Expected: All PASS

- [ ] **Step 2: 运行 lint 检查**

Run: `cd /Users/ericding/code/agriculture/yimatong/frontend && pnpm lint:admin`
Expected: No errors (warnings acceptable)

- [ ] **Step 3: 如有 lint 错误，修复后重新运行**

Run: `cd /Users/ericding/code/agriculture/yimatong/frontend && pnpm lint:admin`
Expected: PASS

- [ ] **Step 4: Commit（如有修复）**

```bash
git add -A
git commit -m "chore: lint fixes from dashboard UX improvement pass"
```

---

## Self-Review Checklist

### 1. Spec Coverage

| Issue | Task | Status |
|-------|------|--------|
| C1: 导出格式不匹配 | Task 1 | ✅ |
| C2: SSE 无重连 | Task 5 | ✅ |
| M1: 空状态引导 | Task 2 | ✅ |
| M2: 日期选择器位置 | Task 3 | ✅ |
| M3: 码批次 loading | Task 4 | ✅ |
| M4: 错误静默吞没 | Task 5 | ✅ |
| M5: useEffect 依赖 | Task 5 | ✅ |
| S1: 刷新按钮 | Task 2 | ✅ |
| S2: 表格跳转 | 部分实现（空状态引导中提供跳转按钮） | ⚠️ |
| S3: 环比指标 | 建议级，可后续迭代 | ⏳ |
| S4: 导出 loading | Task 1/3/4 | ✅ |
| S5: 时间格式化 | Task 2 | ✅ |
| S6: 侧边栏优化 | 超出本次范围，建议独立迭代 | ⏳ |

### 2. Placeholder Scan

无 TBD/TODO/placeholder。所有步骤包含完整代码。

### 3. Type Consistency

- `DashboardData` 接口保持不变，`error` state 为 `string | null`
- 所有 fetch 函数使用 `useCallback` 包裹
- `ScanTrendRow` / `CodeBatch` / `Campaign` 接口与现有代码一致
- Mock 模式与现有测试文件 `channels/__tests__/page.test.tsx` 保持一致
