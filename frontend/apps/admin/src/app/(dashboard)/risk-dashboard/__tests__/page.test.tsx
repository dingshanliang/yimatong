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
    put: (...args: unknown[]) => mockPut(...args),
  },
  extractErrorMessage: (_err: unknown, fallback = "操作失败") => fallback,
}));

const mockPut = vi.fn();
let mockUser = {
  tenant_id: "t1",
  tenant_type: "brand",
  role: "admin",
  acting_tenant_id: null as string | null,
};

vi.mock("@/lib/auth", () => ({
  useAuthStore: (selector: (s: { user: typeof mockUser | null }) => unknown) =>
    selector({ user: mockUser }),
}));

describe("RiskDashboardPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUser = {
      tenant_id: "t1",
      tenant_type: "brand",
      role: "admin",
      acting_tenant_id: null,
    };
    mockGet.mockImplementation((url: string) => {
      if (url === "/risk-dashboard/repeat-scans") {
        return Promise.resolve({ data: { items: [], total: 0 } });
      }
      if (url === "/risk-dashboard/cross-region") {
        return Promise.resolve({
          data: {
            total_clues: 0,
            unresolved_count: 0,
            by_region: [],
            by_detected_city: [],
            by_code: [],
          },
        });
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
    mockPut.mockResolvedValue({ data: {} });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders risk dashboard title and cards", async () => {
    render(<RiskDashboardPage />);
    await waitFor(() =>
      expect(screen.getByText("风控看板")).toBeInTheDocument()
    );
    expect(screen.getByText("重复扫码热点")).toBeInTheDocument();
    expect(screen.getByText("跨区扫码统计")).toBeInTheDocument();
    expect(screen.getByText("渠道健康评分")).toBeInTheDocument();
    expect(screen.getByText("渠道流量与转化归因")).toBeInTheDocument();
    expect(screen.getByText("窜货线索汇总")).toBeInTheDocument();
  });

  it("shows error message when repeat-scans API fails", async () => {
    mockGet.mockImplementation((url: string) => {
      if (url === "/risk-dashboard/repeat-scans") {
        return Promise.reject(new Error("Server error"));
      }
      if (url === "/risk-dashboard/cross-region") {
        return Promise.resolve({
          data: {
            total_clues: 0,
            unresolved_count: 0,
            by_region: [],
            by_detected_city: [],
            by_code: [],
          },
        });
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
    await waitFor(() =>
      expect(mockMessageError).toHaveBeenCalledWith("加载重复扫码数据失败")
    );
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
        return Promise.resolve({
          data: {
            total_clues: 0,
            unresolved_count: 0,
            by_region: [],
            by_detected_city: [],
            by_code: [],
          },
        });
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
    await waitFor(() =>
      expect(mockMessageError).toHaveBeenCalledWith("加载窜货线索数据失败")
    );
  });

  it("does not request diversion details for a viewer", async () => {
    mockUser = {
      tenant_id: "t1",
      tenant_type: "brand",
      role: "viewer",
      acting_tenant_id: null,
    };
    render(<RiskDashboardPage />);
    await waitFor(() =>
      expect(screen.getByText("风控看板")).toBeInTheDocument()
    );
    expect(mockGet).not.toHaveBeenCalledWith(
      "/risk-dashboard/diversion-summary",
      expect.anything()
    );
  });
});
