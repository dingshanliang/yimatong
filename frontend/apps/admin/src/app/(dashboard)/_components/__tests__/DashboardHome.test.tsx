import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import DashboardHome from "../../_components/DashboardHome";

const mockMessageError = vi.fn();

vi.mock("antd", async () => {
  const actual = await vi.importActual<typeof import("antd")>("antd");
  return {
    ...actual,
    App: {
      useApp: () => ({
        message: { error: mockMessageError },
      }),
    },
  };
});

const mockGet = vi.fn();

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
  },
  extractErrorMessage: (_err: unknown, fallback = "操作失败") => fallback,
}));

vi.mock("@ant-design/charts", () => ({
  Line: () => null,
  Pie: () => null,
}));

const mockPush = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
}));

function mockResponseForUrl(url: string) {
  if (url === "/analytics/dashboard") {
    return Promise.resolve({ data: mockDashboard() });
  }
  if (url === "/analytics/conversion-funnel") {
    return Promise.resolve({
      data: {
        steps: [
          { name: "扫码", value: 100, rate: 100 },
          { name: "领券", value: 25, rate: 25 },
          { name: "核销", value: 10, rate: 10 },
        ],
        period_days: 30,
      },
    });
  }
  if (url === "/analytics/scan-stats") {
    return Promise.resolve({ data: [] });
  }
  if (url === "/analytics/campaign-ranking") {
    return Promise.resolve({
      data: {
        items: [
          {
            campaign_id: "campaign-1",
            campaign_name: "E2E Campaign",
            campaign_status: "draft",
            scan_count: 10,
            claim_count: 0,
            conversion_rate: 0,
          },
        ],
      },
    });
  }
  if (url === "/channel-analytics/health-scores") {
    return Promise.resolve({ data: { scores: [] } });
  }
  if (url === "/analytics/recent-events") {
    return Promise.resolve({ data: { events: [] } });
  }
  if (url === "/analytics/alerts") {
    return Promise.resolve({ data: { alerts: [] } });
  }
  return Promise.resolve({ data: {} });
}

function mockDashboard() {
  return {
    today_scans: 10,
    today_uv: 7,
    cumulative_scans: 100,
    cumulative_first_scans: 80,
    period_claim_count: 6,
    period_claim_rate: 12,
    trend: [],
    environment_breakdown: {},
    comparison: {
      weekly_scans_change: { value: 8, direction: "up" },
      weekly_first_scans_change: null,
    },
  };
}

function mockApiDefaults() {
  mockGet.mockImplementation(mockResponseForUrl);
}

describe("DashboardHome", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockApiDefaults();
  });

  it("renders dashboard statistics after loading", async () => {
    render(<DashboardHome />);

    await waitFor(() => expect(screen.getByText("经营看板")).toBeInTheDocument());
    expect(screen.getByText("今日扫码")).toBeInTheDocument();
    expect(screen.getByText("今日 UV")).toBeInTheDocument();
    expect(screen.getByText("累计扫码")).toBeInTheDocument();
    expect(screen.getByText("期间领券")).toBeInTheDocument();
    expect(screen.getAllByText("10").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("100").length).toBeGreaterThanOrEqual(1);
  });

  it("renders the main dashboard widgets", async () => {
    render(<DashboardHome />);

    await waitFor(() => expect(screen.getByText("核心转化漏斗（近 30 天）")).toBeInTheDocument());
    expect(screen.getByText("扫码趋势")).toBeInTheDocument();
    expect(screen.getByText("活动排行 Top 5")).toBeInTheDocument();
    expect(screen.getByText("渠道健康 Top 5")).toBeInTheDocument();
    expect(screen.getByText("最近动态")).toBeInTheDocument();
  });

  it("handles an empty conversion funnel response without crashing", async () => {
    mockGet.mockImplementation((url: string) => {
      if (url === "/analytics/conversion-funnel") {
        return Promise.resolve({ data: {} });
      }
      return mockResponseForUrl(url);
    });

    render(<DashboardHome />);

    await waitFor(() => expect(screen.getByText("暂无转化数据")).toBeInTheDocument());
  });

  it("shows an error message when dashboard API fails", async () => {
    mockGet.mockImplementation((url: string) => {
      if (url === "/analytics/dashboard") {
        return Promise.reject(new Error("Network error"));
      }
      if (url === "/analytics/conversion-funnel") {
        return Promise.resolve({ data: { steps: [] } });
      }
      return mockResponseForUrl(url);
    });

    render(<DashboardHome />);

    await waitFor(() => expect(mockMessageError).toHaveBeenCalledWith("加载工作台数据失败"));
  });

  it("reloads data from the refresh button", async () => {
    render(<DashboardHome />);
    await waitFor(() => expect(screen.getByText("今日扫码")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /刷新/ }));

    await waitFor(() => {
      const dashboardCalls = mockGet.mock.calls.filter((call: unknown[]) => call[0] === "/analytics/dashboard");
      expect(dashboardCalls.length).toBeGreaterThanOrEqual(2);
    });
  });
});
