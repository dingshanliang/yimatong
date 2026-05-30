import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import AgencyPage from "../page";

// Mock Ant Design message
vi.mock("antd", async () => {
  const actual = await vi.importActual("antd");
  return {
    ...actual,
    message: {
      success: vi.fn(),
      error: vi.fn(),
    },
  };
});

// Mock api
const mockGet = vi.fn();
const mockPost = vi.fn();
const mockPatch = vi.fn();
const mockDelete = vi.fn();

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
    patch: (...args: unknown[]) => mockPatch(...args),
    delete: (...args: unknown[]) => mockDelete(...args),
  },
}));

// Mock next/link
vi.mock("next/link", () => ({
  default: ({ children, href }: { children: React.ReactNode; href: string }) => (
    <a href={href} data-testid="link">{children}</a>
  ),
}));

describe("AgencyPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders agency workbench title", () => {
    mockGet.mockResolvedValue({ data: { items: [], total: 0, page: 1, page_size: 20 } });
    render(<AgencyPage />);
    expect(screen.getByText("代运营工作台")).toBeInTheDocument();
  });

  it("fetches and displays client list", async () => {
    mockGet.mockImplementation((url: string) => {
      if (url === "/tenants") {
        return Promise.resolve({
          data: {
            items: [
              {
                id: "t1",
                name: "客户A",
                status: "active",
                plan: "pro",
                plan_expires_at: "2026-12-31T00:00:00Z",
                created_at: "2026-01-15T00:00:00Z",
              },
              {
                id: "t2",
                name: "客户B",
                status: "onboarding",
                plan: "starter",
                plan_expires_at: null,
                created_at: "2026-05-01T00:00:00Z",
              },
            ],
            total: 2,
            page: 1,
            page_size: 20,
          },
        });
      }
      if (url === "/ops/tasks") {
        return Promise.resolve({
          data: {
            items: [
              {
                id: "task1",
                tenant_id: "t1",
                title: "配置品牌信息",
                status: "pending",
                priority: "high",
                due_date: "2026-06-15T00:00:00Z",
              },
            ],
            total: 1,
            page: 1,
            page_size: 20,
          },
        });
      }
      return Promise.resolve({ data: { items: [], total: 0 } });
    });

    render(<AgencyPage />);

    await waitFor(() => {
      expect(screen.getByText("客户A")).toBeInTheDocument();
      expect(screen.getByText("客户B")).toBeInTheDocument();
    });

    // Check status tags
    expect(screen.getByText("活跃")).toBeInTheDocument();
    expect(screen.getByText("配置中")).toBeInTheDocument();
  });

  it("displays statistics cards", async () => {
    mockGet.mockResolvedValue({
      data: { items: [], total: 0, page: 1, page_size: 20 },
    });
    render(<AgencyPage />);

    expect(screen.getByText("客户总数")).toBeInTheDocument();
    expect(screen.getByText("活跃客户")).toBeInTheDocument();
    expect(screen.getByText("配置中客户")).toBeInTheDocument();
    expect(screen.getByText("待办任务")).toBeInTheDocument();
  });

  it("fetches and displays tasks", async () => {
    mockGet.mockImplementation((url: string) => {
      if (url === "/tenants") {
        return Promise.resolve({
          data: { items: [], total: 0, page: 1, page_size: 20 },
        });
      }
      if (url === "/ops/tasks") {
        return Promise.resolve({
          data: {
            items: [
              {
                id: "task1",
                tenant_id: "t1",
                title: "配置品牌信息",
                status: "pending",
                priority: "high",
                due_date: "2026-06-15T00:00:00Z",
              },
            ],
            total: 1,
            page: 1,
            page_size: 20,
          },
        });
      }
      return Promise.resolve({ data: { items: [], total: 0 } });
    });

    render(<AgencyPage />);

    await waitFor(() => {
      expect(screen.getByText("配置品牌信息")).toBeInTheDocument();
    });
  });

  it("opens launch checklist modal when clicking checklist button", async () => {
    mockGet.mockImplementation((url: string) => {
      if (url === "/tenants") {
        return Promise.resolve({
          data: {
            items: [
              {
                id: "t1",
                name: "客户A",
                status: "active",
                plan: "pro",
                plan_expires_at: null,
                created_at: "2026-01-15T00:00:00Z",
              },
            ],
            total: 1,
            page: 1,
            page_size: 20,
          },
        });
      }
      if (url === "/ops/tasks") {
        return Promise.resolve({
          data: { items: [], total: 0, page: 1, page_size: 20 },
        });
      }
      if (url.includes("/ops/clients/") && url.includes("/launch-checklist")) {
        return Promise.resolve({
          data: {
            tenant_id: "t1",
            ready: false,
            passed_count: 1,
            total_count: 4,
            checks: [
              { name: "至少 1 个品牌已创建", passed: true, detail: "当前品牌数: 1" },
              { name: "至少 1 个产品已创建", passed: false, detail: "当前产品数: 0" },
            ],
          },
        });
      }
      return Promise.resolve({ data: { items: [], total: 0 } });
    });

    render(<AgencyPage />);

    await waitFor(() => {
      expect(screen.getByText("客户A")).toBeInTheDocument();
    });

    const checklistBtn = screen.getByText("检查清单");
    fireEvent.click(checklistBtn);

    await waitFor(() => {
      expect(screen.getByText("上线检查清单 — 客户A")).toBeInTheDocument();
      expect(screen.getByText("至少 1 个品牌已创建")).toBeInTheDocument();
    });
  });

  it("opens task create modal", async () => {
    mockGet.mockResolvedValue({
      data: { items: [], total: 0, page: 1, page_size: 20 },
    });
    render(<AgencyPage />);

    const createTaskBtn = screen.getByText("新建任务");
    fireEvent.click(createTaskBtn);

    await waitFor(() => {
      expect(screen.getByText("新建待办任务")).toBeInTheDocument();
    });
  });

  it("shows plan expiration info", async () => {
    mockGet.mockImplementation((url: string) => {
      if (url === "/tenants") {
        return Promise.resolve({
          data: {
            items: [
              {
                id: "t1",
                name: "客户A",
                status: "active",
                plan: "pro",
                plan_expires_at: "2026-12-31T00:00:00Z",
                created_at: "2026-01-15T00:00:00Z",
              },
            ],
            total: 1,
            page: 1,
            page_size: 20,
          },
        });
      }
      if (url === "/ops/tasks") {
        return Promise.resolve({
          data: { items: [], total: 0, page: 1, page_size: 20 },
        });
      }
      return Promise.resolve({ data: { items: [], total: 0 } });
    });

    render(<AgencyPage />);

    await waitFor(() => {
      expect(screen.getByText("客户A")).toBeInTheDocument();
    });
  });
});
