import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import AgencyPage from "../page";

const mockConfirm = vi.fn();
// Mock Ant Design App
vi.mock("antd", async () => {
  const actual = await vi.importActual("antd");
  return {
    ...actual,
    App: {
      ...actual.App,
      useApp: () => ({
        message: { success: vi.fn(), error: vi.fn() },
        modal: { confirm: mockConfirm },
      }),
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
      expect(screen.getAllByText("客户A").length).toBeGreaterThanOrEqual(1);
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

  it("renders task action buttons for pending tasks", async () => {
    mockGet.mockImplementation((url: string) => {
      if (url === "/tenants") {
        return Promise.resolve({
          data: { items: [{ id: "t1", name: "客户A", status: "active", plan: "pro", plan_expires_at: null, created_at: "2026-01-15T00:00:00Z" }], total: 1, page: 1, page_size: 20 },
        });
      }
      if (url === "/ops/tasks") {
        return Promise.resolve({
          data: {
            items: [{ id: "task1", tenant_id: "t1", title: "配置品牌", status: "pending", priority: "high", due_date: null }],
            total: 1, page: 1, page_size: 20,
          },
        });
      }
      return Promise.resolve({ data: { items: [], total: 0 } });
    });

    render(<AgencyPage />);

    await waitFor(() => {
      expect(screen.getByText("开始")).toBeInTheDocument();
      expect(screen.getByText("取消")).toBeInTheDocument();
    });
  });

  it("renders complete button for in_progress tasks", async () => {
    mockGet.mockImplementation((url: string) => {
      if (url === "/tenants") {
        return Promise.resolve({ data: { items: [], total: 0, page: 1, page_size: 20 } });
      }
      if (url === "/ops/tasks") {
        return Promise.resolve({
          data: {
            items: [{ id: "task2", tenant_id: "t1", title: "配置页面", status: "in_progress", priority: "medium", due_date: null }],
            total: 1, page: 1, page_size: 20,
          },
        });
      }
      return Promise.resolve({ data: { items: [], total: 0 } });
    });

    render(<AgencyPage />);

    await waitFor(() => {
      expect(screen.getByText("完成")).toBeInTheDocument();
    });
  });

  it("renders delete button for completed tasks", async () => {
    mockGet.mockImplementation((url: string) => {
      if (url === "/tenants") {
        return Promise.resolve({ data: { items: [], total: 0, page: 1, page_size: 20 } });
      }
      if (url === "/ops/tasks") {
        return Promise.resolve({
          data: {
            items: [{ id: "task3", tenant_id: "t1", title: "已完成任务", status: "completed", priority: "low", due_date: null }],
            total: 1, page: 1, page_size: 20,
          },
        });
      }
      return Promise.resolve({ data: { items: [], total: 0 } });
    });

    render(<AgencyPage />);

    await waitFor(() => {
      const deleteButtons = screen.getAllByText("删除");
      expect(deleteButtons.length).toBeGreaterThan(0);
    });
  });

  it("calls PATCH /ops/tasks/:id with correct status when clicking start", async () => {
    mockPatch.mockResolvedValue({ data: { id: "task1", status: "in_progress" } });
    mockGet.mockImplementation((url: string) => {
      if (url === "/tenants") {
        return Promise.resolve({
          data: { items: [{ id: "t1", name: "客户A", status: "active", plan: "pro", plan_expires_at: null, created_at: "2026-01-15T00:00:00Z" }], total: 1, page: 1, page_size: 20 },
        });
      }
      if (url === "/ops/tasks") {
        return Promise.resolve({
          data: {
            items: [{ id: "task1", tenant_id: "t1", title: "配置品牌", status: "pending", priority: "high", due_date: null }],
            total: 1, page: 1, page_size: 20,
          },
        });
      }
      return Promise.resolve({ data: { items: [], total: 0 } });
    });

    render(<AgencyPage />);

    const startBtn = await screen.findByText("开始");
    fireEvent.click(startBtn);

    await waitFor(() => {
      expect(mockPatch).toHaveBeenCalledWith("/ops/tasks/task1", { status: "in_progress" });
    });
  });

  it("calls PATCH with completed status when clicking complete on in_progress task", async () => {
    mockPatch.mockResolvedValue({ data: { id: "task2", status: "completed" } });
    mockGet.mockImplementation((url: string) => {
      if (url === "/tenants") {
        return Promise.resolve({ data: { items: [], total: 0, page: 1, page_size: 20 } });
      }
      if (url === "/ops/tasks") {
        return Promise.resolve({
          data: {
            items: [{ id: "task2", tenant_id: "t1", title: "配置页面", status: "in_progress", priority: "medium", due_date: null }],
            total: 1, page: 1, page_size: 20,
          },
        });
      }
      return Promise.resolve({ data: { items: [], total: 0 } });
    });

    render(<AgencyPage />);

    const completeBtn = await screen.findByText("完成");
    fireEvent.click(completeBtn);

    await waitFor(() => {
      expect(mockPatch).toHaveBeenCalledWith("/ops/tasks/task2", { status: "completed" });
    });
  });

  it("calls DELETE /ops/tasks/:id when confirming delete on completed task", async () => {
    mockDelete.mockResolvedValue({ status: 204 });
    mockGet.mockImplementation((url: string) => {
      if (url === "/tenants") {
        return Promise.resolve({ data: { items: [], total: 0, page: 1, page_size: 20 } });
      }
      if (url === "/ops/tasks") {
        return Promise.resolve({
          data: {
            items: [{ id: "task3", tenant_id: "t1", title: "已完成任务", status: "completed", priority: "low", due_date: null }],
            total: 1, page: 1, page_size: 20,
          },
        });
      }
      return Promise.resolve({ data: { items: [], total: 0 } });
    });

    render(<AgencyPage />);

    const deleteBtn = await screen.findByText("删除");
    fireEvent.click(deleteBtn);

    await waitFor(() => {
      expect(mockConfirm).toHaveBeenCalled();
    });
    const onOk = mockConfirm.mock.calls[0][0].onOk;
    await onOk();
    expect(mockDelete).toHaveBeenCalledWith("/ops/tasks/task3");
  });

  it("renders client search input and task filter selects", async () => {
    mockGet.mockResolvedValue({ data: { items: [], total: 0, page: 1, page_size: 20 } });
    render(<AgencyPage />);

    expect(screen.getByText("客户列表")).toBeInTheDocument();
    expect(screen.getByText("任务列表")).toBeInTheDocument();
  });

  it("shows related client name in task table", async () => {
    mockGet.mockImplementation((url: string) => {
      if (url === "/ops/overview") {
        return Promise.resolve({ data: { total_clients: 1, active_clients: 1, onboarding_clients: 0, pending_tasks: 1 } });
      }
      if (url === "/tenants") {
        return Promise.resolve({
          data: { items: [{ id: "t1", name: "测试品牌", status: "active", plan: "pro", plan_expires_at: null, created_at: "2026-01-15T00:00:00Z" }], total: 1, page: 1, page_size: 20 },
        });
      }
      if (url === "/ops/tasks") {
        return Promise.resolve({
          data: {
            items: [{ id: "task1", tenant_id: "t1", tenant_name: "测试品牌", title: "配置品牌", status: "pending", priority: "high", due_date: null }],
            total: 1, page: 1, page_size: 20,
          },
        });
      }
      return Promise.resolve({ data: { items: [], total: 0 } });
    });

    render(<AgencyPage />);

    await waitFor(() => {
      const clientNameCells = screen.getAllByText("测试品牌");
      expect(clientNameCells.length).toBeGreaterThanOrEqual(2);
    });
  });

  it("shows generated admin credentials after client initialization", async () => {
    mockGet.mockImplementation((url: string) => {
      if (url === "/ops/overview") {
        return Promise.resolve({ data: { total_clients: 0, active_clients: 0, onboarding_clients: 0, pending_tasks: 0 } });
      }
      return Promise.resolve({ data: { items: [], total: 0, page: 1, page_size: 20 } });
    });
    mockPost.mockResolvedValue({
      data: {
        id: "tenant-1",
        name: "新客户",
        admin_email: "admin@new.test",
        initial_password: "Ymt-NewClient123",
      },
    });

    render(<AgencyPage />);
    fireEvent.click(screen.getByRole("button", { name: /初始化新客户/ }));
    fireEvent.change(screen.getByLabelText("客户名称"), { target: { value: "新客户" } });
    fireEvent.change(screen.getByLabelText("联系人"), { target: { value: "张三" } });
    fireEvent.change(screen.getByLabelText("联系电话"), { target: { value: "13800000000" } });
    fireEvent.change(screen.getByLabelText("联系邮箱"), { target: { value: "admin@new.test" } });

    fireEvent.click(screen.getByRole("button", { name: "下一步" }));
    fireEvent.change(await screen.findByLabelText("品牌名称"), { target: { value: "新品牌" } });
    fireEvent.click(screen.getByRole("button", { name: "下一步" }));
    await screen.findByLabelText("扫码页模板");
    fireEvent.click(screen.getByRole("button", { name: "下一步" }));
    await screen.findByText("配置确认");
    fireEvent.click(await screen.findByRole("button", { name: "完成初始化" }));

    await waitFor(() => {
      expect(screen.getByTestId("agency-init-credentials")).toHaveTextContent("admin@new.test");
      expect(screen.getByTestId("agency-init-credentials")).toHaveTextContent("Ymt-NewClient123");
    });
  });
});
