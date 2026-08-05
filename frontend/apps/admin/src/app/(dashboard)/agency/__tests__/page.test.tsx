import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import AgencyPage from "../page";

const mockConfirm = vi.fn();
const mockSuccess = vi.fn();
const mockError = vi.fn();
const mockPush = vi.fn();
const mockSwitchAgencyContext = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
}));

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
  registerAuthInterceptorHandlers: vi.fn(),
}));

vi.mock("@/lib/auth", () => ({
  useAuthStore: {
    getState: () => ({
      switchAgencyContext: mockSwitchAgencyContext,
      user: { agency_scope: ["products"] },
    }),
  },
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
    return Promise.resolve({
      data: { items: [], total: 0, page: 1, page_size: 20 },
    });
  });
}

describe("AgencyPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSwitchAgencyContext.mockResolvedValue(undefined);
    mockWorkbench();
  });

  it("renders agency workbench title", () => {
    render(<AgencyPage />);
    expect(screen.getByText("代运营工作台")).toBeInTheDocument();
  });

  it("loads the operator queue from the workbench endpoint", async () => {
    render(<AgencyPage />);

    await waitFor(() => {
      expect(mockGet).toHaveBeenCalledWith("/ops/workbench", {
        params: { page: 1, page_size: 100 },
      });
      expect(screen.getAllByText("客户A").length).toBeGreaterThanOrEqual(1);
      expect(screen.getByText("2/4")).toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: /发布扫码页/ })
      ).toBeInTheDocument();
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
      expect(
        screen.getByText("缺：发布扫码页、激活码批次")
      ).toBeInTheDocument();
      expect(screen.getByText("待办 1")).toBeInTheDocument();
      expect(screen.getByText("高优先级 1")).toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: /上线检查/ })
      ).toBeInTheDocument();
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

  it("opens the module linked by the client next action", async () => {
    render(<AgencyPage />);

    fireEvent.click(await screen.findByRole("button", { name: /发布扫码页/ }));

    await waitFor(() => {
      expect(mockPush).toHaveBeenCalledWith("/pages");
    });
  });

  it("enters an authorized client before navigating to its first module", async () => {
    mockWorkbench({
      clients: [
        client({ agency_scope: ["products"], full_workbench_access: false }),
      ],
    });
    render(<AgencyPage />);

    fireEvent.click(await screen.findByRole("button", { name: /进入管理/ }));

    await waitFor(() => {
      expect(mockSwitchAgencyContext).toHaveBeenCalledWith("t1");
      expect(mockPush).toHaveBeenCalledWith("/products");
    });
  });

  it("refreshes the workbench and asks for reauthorization when entering returns 403", async () => {
    mockWorkbench({
      clients: [
        client({ agency_scope: ["products"], full_workbench_access: false }),
      ],
    });
    mockSwitchAgencyContext.mockRejectedValue({ response: { status: 403 } });
    render(<AgencyPage />);
    await screen.findAllByText("客户A");
    const callsBeforeEnter = mockGet.mock.calls.length;

    fireEvent.click(screen.getByRole("button", { name: /进入管理/ }));

    await waitFor(() => {
      expect(mockGet.mock.calls.length).toBeGreaterThan(callsBeforeEnter);
      expect(mockError).toHaveBeenCalledWith(
        "该客户的代运营授权已失效，请联系客户重新授权后再进入"
      );
      expect(mockPush).not.toHaveBeenCalled();
    });
  });

  it("stays on the workbench and allows retry after a network failure", async () => {
    mockWorkbench({
      clients: [
        client({ agency_scope: ["products"], full_workbench_access: false }),
      ],
    });
    mockSwitchAgencyContext
      .mockRejectedValueOnce(new Error("network"))
      .mockResolvedValueOnce(undefined);
    render(<AgencyPage />);

    fireEvent.click(await screen.findByRole("button", { name: /进入管理/ }));
    await waitFor(() => {
      expect(mockError).toHaveBeenCalledWith("进入客户失败，请检查网络后重试");
      expect(mockPush).not.toHaveBeenCalled();
    });
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /进入管理/ })).toBeEnabled();
    });

    fireEvent.click(screen.getByRole("button", { name: /进入管理/ }));
    await waitFor(() => {
      expect(mockSwitchAgencyContext).toHaveBeenCalledTimes(2);
      expect(mockPush).toHaveBeenCalledWith("/products");
    });
  });

  it("creates a prefilled task for the selected client", async () => {
    mockPost.mockResolvedValue({ data: { id: "task-new" } });
    render(<AgencyPage />);

    fireEvent.click(await screen.findByRole("button", { name: "创建任务" }));
    await screen.findByText("新建待办任务");
    expect(screen.getByLabelText("任务标题")).toHaveValue("为客户A发布扫码页");
    fireEvent.click(screen.getByTestId("agency-task-create-submit"));

    await waitFor(() => {
      expect(mockPost).toHaveBeenCalledWith("/ops/tasks", {
        tenant_id: "t1",
        title: "为客户A发布扫码页",
        description: null,
        priority: "medium",
      });
    });
  });

  it("updates pending task status to in progress", async () => {
    mockPatch.mockResolvedValue({
      data: { id: "task1", status: "in_progress" },
    });
    render(<AgencyPage />);

    fireEvent.click(await screen.findByText("开始"));

    await waitFor(() => {
      expect(mockPatch).toHaveBeenCalledWith("/ops/tasks/task1", {
        status: "in_progress",
      });
      expect(mockSuccess).toHaveBeenCalledWith("任务状态已更新");
    });
  });

  it("updates in-progress task status to completed", async () => {
    mockWorkbench({
      tasks: [
        task({
          id: "task2",
          title: "配置页面",
          status: "in_progress",
          priority: "medium",
        }),
      ],
    });
    mockPatch.mockResolvedValue({ data: { id: "task2", status: "completed" } });
    render(<AgencyPage />);

    fireEvent.click(await screen.findByText("完成"));

    await waitFor(() => {
      expect(mockPatch).toHaveBeenCalledWith("/ops/tasks/task2", {
        status: "completed",
      });
    });
  });

  it("deletes completed tasks after confirmation", async () => {
    mockWorkbench({
      tasks: [
        task({
          id: "task3",
          title: "已完成任务",
          status: "completed",
          priority: "low",
        }),
      ],
    });
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
      expect(
        screen.getByRole("button", { name: /重新加载/ })
      ).toBeInTheDocument();
    });
  });

  it("explains when filters return no clients", async () => {
    mockWorkbench({
      clients: [],
      tasks: [],
      workbenchSummary: summary({
        total_clients: 1,
        ready_clients: 1,
        blocked_clients: 0,
        pending_tasks: 0,
      }),
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
      workbenchSummary: summary({
        total_clients: 0,
        active_clients: 0,
        ready_clients: 0,
        blocked_clients: 0,
        pending_tasks: 0,
      }),
    });

    render(<AgencyPage />);

    await waitFor(() => {
      expect(screen.getByText("还没有客户")).toBeInTheDocument();
      expect(
        screen.getByText("初始化新客户后，这里会显示上线准备度和下一步动作。")
      ).toBeInTheDocument();
    });
  });

  it("sends workbench filters to the backend", async () => {
    render(<AgencyPage />);

    const searchInput = screen.getByPlaceholderText("搜索客户名称");
    fireEvent.change(searchInput, { target: { value: "客户A" } });
    fireEvent.keyDown(searchInput, { key: "Enter", code: "Enter" });

    await waitFor(() => {
      expect(mockGet).toHaveBeenCalledWith("/ops/workbench", {
        params: { page: 1, page_size: 100, q: "客户A" },
      });
    });
  });

  it("does not expose direct tenant creation from the agency workbench", async () => {
    render(<AgencyPage />);

    await waitFor(() => {
      expect(screen.getByText("代运营工作台")).toBeInTheDocument();
    });
    expect(
      screen.queryByRole("button", { name: /初始化新客户/ })
    ).not.toBeInTheDocument();
    expect(mockPost).not.toHaveBeenCalledWith("/tenants", expect.anything());
  });
});
