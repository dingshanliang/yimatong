import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import AgencyPage from "../page";

const mockConfirm = vi.fn();
const mockSuccess = vi.fn();
const mockError = vi.fn();

vi.mock("antd", async () => {
  const actual = await vi.importActual("antd");
  return {
    ...actual,
    App: {
      ...actual.App,
      useApp: () => ({
        message: { success: mockSuccess, error: mockError },
        modal: { confirm: mockConfirm },
      }),
    },
  };
});

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
  extractErrorMessage: (_error: unknown, fallback: string) => fallback,
}));

function summary(overrides = {}) {
  return {
    total_clients: 1,
    active_clients: 1,
    ready_clients: 0,
    blocked_clients: 1,
    pending_tasks: 1,
    in_progress_tasks: 0,
    overdue_tasks: 0,
    ...overrides,
  };
}

function client(overrides = {}) {
  return {
    id: "t1",
    name: "客户A",
    status: "active",
    plan: "pro",
    plan_expires_at: null,
    created_at: "2026-01-15T00:00:00Z",
    readiness: {
      ready: false,
      passed_count: 2,
      total_count: 4,
      percent: 50,
      missing_keys: ["page_published", "code_batch_activated"],
      missing_labels: ["发布扫码页", "激活码批次"],
    },
    task_summary: { pending: 1, in_progress: 0, overdue: 0, high_priority: 1 },
    next_action: {
      type: "page_published",
      label: "发布扫码页",
      href: "/pages",
      task_title: "为客户A发布扫码页",
    },
    ...overrides,
  };
}

function task(overrides = {}) {
  return {
    id: "task1",
    tenant_id: "t1",
    tenant_name: "客户A",
    title: "配置品牌信息",
    status: "pending",
    priority: "high",
    due_date: "2026-06-15T00:00:00Z",
    overdue: false,
    ...overrides,
  };
}

function mockWorkbench({
  clients = [client()],
  tasks = [task()],
  workbenchSummary = summary(),
  checklist = {
    tenant_id: "t1",
    ready: false,
    passed_count: 1,
    total_count: 4,
    checks: [
      { name: "至少 1 个品牌已创建", passed: true, detail: "当前品牌数: 1" },
      { name: "至少 1 个产品已创建", passed: false, detail: "当前产品数: 0" },
    ],
  },
} = {}) {
  mockGet.mockImplementation((url: string) => {
    if (url === "/ops/workbench") {
      return Promise.resolve({
        data: {
          summary: workbenchSummary,
          clients,
          tasks,
          total: workbenchSummary.total_clients,
          page: 1,
          page_size: 100,
        },
      });
    }
    if (url.includes("/ops/clients/") && url.includes("/launch-checklist")) {
      return Promise.resolve({ data: checklist });
    }
    return Promise.resolve({ data: { items: [], total: 0, page: 1, page_size: 20 } });
  });
}

describe("AgencyPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockWorkbench();
  });

  it("renders agency workbench title", () => {
    render(<AgencyPage />);
    expect(screen.getByText("代运营工作台")).toBeInTheDocument();
  });

  it("loads the operator queue from the workbench endpoint", async () => {
    render(<AgencyPage />);

    await waitFor(() => {
      expect(mockGet).toHaveBeenCalledWith("/ops/workbench", { params: { page: 1, page_size: 100 } });
      expect(screen.getAllByText("客户A").length).toBeGreaterThanOrEqual(1);
      expect(screen.getByText("2/4")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /发布扫码页/ })).toBeInTheDocument();
    });
  });

  it("shows operator summary cards", async () => {
    render(<AgencyPage />);

    await waitFor(() => {
      expect(screen.getByText("客户总数")).toBeInTheDocument();
      expect(screen.getByText("已具备上线条件")).toBeInTheDocument();
      expect(screen.getByText("需补齐配置")).toBeInTheDocument();
      expect(screen.getByText("逾期任务")).toBeInTheDocument();
      expect(screen.getByText("待办任务")).toBeInTheDocument();
    });
  });

  it("shows launch readiness, missing items, and next action in the client queue", async () => {
    render(<AgencyPage />);

    await waitFor(() => {
      expect(screen.getByText("上线准备度")).toBeInTheDocument();
      expect(screen.getByText("缺：发布扫码页、激活码批次")).toBeInTheDocument();
      expect(screen.getByText("待办 1")).toBeInTheDocument();
      expect(screen.getByText("高优先级 1")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /上线检查/ })).toBeInTheDocument();
    });
  });

  it("opens launch checklist modal from a client row", async () => {
    render(<AgencyPage />);

    fireEvent.click(await screen.findByRole("button", { name: /上线检查/ }));

    await waitFor(() => {
      expect(mockGet).toHaveBeenCalledWith("/ops/clients/t1/launch-checklist");
      expect(screen.getByText("上线检查清单 — 客户A")).toBeInTheDocument();
      expect(screen.getByText("至少 1 个品牌已创建")).toBeInTheDocument();
    });
  });

  it("prefills task creation from the row next action", async () => {
    render(<AgencyPage />);

    fireEvent.click(await screen.findByRole("button", { name: /发布扫码页/ }));

    await waitFor(() => {
      expect(screen.getByText("新建待办任务")).toBeInTheDocument();
      expect(screen.getByLabelText("任务标题")).toHaveValue("为客户A发布扫码页");
    });
  });

  it("creates a prefilled task for the selected client", async () => {
    mockPost.mockResolvedValue({ data: { id: "task-new" } });
    render(<AgencyPage />);

    fireEvent.click(await screen.findByRole("button", { name: /发布扫码页/ }));
    await screen.findByText("新建待办任务");
    fireEvent.click(screen.getByTestId("agency-task-create-submit"));

    await waitFor(() => {
      expect(mockPost).toHaveBeenCalledWith("/ops/tasks", {
        tenant_id: "t1",
        title: "为客户A发布扫码页",
        priority: "medium",
      });
    });
  });

  it("updates pending task status to in progress", async () => {
    mockPatch.mockResolvedValue({ data: { id: "task1", status: "in_progress" } });
    render(<AgencyPage />);

    fireEvent.click(await screen.findByText("开始"));

    await waitFor(() => {
      expect(mockPatch).toHaveBeenCalledWith("/ops/tasks/task1", { status: "in_progress" });
      expect(mockSuccess).toHaveBeenCalledWith("任务状态已更新");
    });
  });

  it("updates in-progress task status to completed", async () => {
    mockWorkbench({ tasks: [task({ id: "task2", title: "配置页面", status: "in_progress", priority: "medium" })] });
    mockPatch.mockResolvedValue({ data: { id: "task2", status: "completed" } });
    render(<AgencyPage />);

    fireEvent.click(await screen.findByText("完成"));

    await waitFor(() => {
      expect(mockPatch).toHaveBeenCalledWith("/ops/tasks/task2", { status: "completed" });
    });
  });

  it("deletes completed tasks after confirmation", async () => {
    mockWorkbench({ tasks: [task({ id: "task3", title: "已完成任务", status: "completed", priority: "low" })] });
    mockDelete.mockResolvedValue({ status: 204 });
    render(<AgencyPage />);

    fireEvent.click(await screen.findByText("删除"));
    const onOk = mockConfirm.mock.calls[0][0].onOk;
    await onOk();

    expect(mockDelete).toHaveBeenCalledWith("/ops/tasks/task3");
  });

  it("explains a failed workbench load", async () => {
    mockGet.mockRejectedValue(new Error("network"));

    render(<AgencyPage />);

    await waitFor(() => {
      expect(screen.getByText("工作台数据加载失败")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /重新加载/ })).toBeInTheDocument();
    });
  });

  it("explains when filters return no clients", async () => {
    mockWorkbench({
      clients: [],
      tasks: [],
      workbenchSummary: summary({ total_clients: 1, ready_clients: 1, blocked_clients: 0, pending_tasks: 0 }),
    });

    render(<AgencyPage />);

    await waitFor(() => {
      expect(screen.getByText("没有符合当前条件的客户")).toBeInTheDocument();
    });
  });

  it("shows no-client empty state", async () => {
    mockWorkbench({
      clients: [],
      tasks: [],
      workbenchSummary: summary({ total_clients: 0, active_clients: 0, ready_clients: 0, blocked_clients: 0, pending_tasks: 0 }),
    });

    render(<AgencyPage />);

    await waitFor(() => {
      expect(screen.getByText("还没有客户")).toBeInTheDocument();
      expect(screen.getByText("初始化新客户后，这里会显示上线准备度和下一步动作。")).toBeInTheDocument();
    });
  });

  it("sends workbench filters to the backend", async () => {
    render(<AgencyPage />);

    fireEvent.change(screen.getByPlaceholderText("搜索客户名称"), { target: { value: "客户A" } });
    fireEvent.click(screen.getByRole("button", { name: "search" }));

    await waitFor(() => {
      expect(mockGet).toHaveBeenCalledWith("/ops/workbench", {
        params: { page: 1, page_size: 100, q: "客户A" },
      });
    });
  });

  it("shows generated admin credentials after client initialization", async () => {
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
