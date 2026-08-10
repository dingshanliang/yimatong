import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AccountsPage from "../page";

const mockMessage = { success: vi.fn(), error: vi.fn() };
const mockGet = vi.fn();
const mockPost = vi.fn();
const mockPatch = vi.fn();
const mockDelete = vi.fn();
const mockClipboardWriteText = vi.fn();
const mockRetryAccounts = vi.fn();
const mockRetryRoles = vi.fn();
let mockCurrentRole = "admin";
let mockAccountsError: unknown;
let mockRolesError: unknown;
let mockAccounts = [
  {
    id: "acct-1",
    email: "sales@test.com",
    name: "销售账号",
    organization_id: "org-1",
    organization_name: "销售部",
    is_active: true,
    roles: [{ id: "role-operator", name: "operator" }],
  },
];
const mockRoles = [
  { id: "role-admin", name: "admin" },
  { id: "role-operator", name: "operator" },
  { id: "role-viewer", name: "viewer" },
];

vi.mock("antd", async () => {
  const actual = await vi.importActual<typeof import("antd")>("antd");
  return {
    ...actual,
    App: {
      ...actual.App,
      useApp: () => ({
        message: mockMessage,
        modal: {
          confirm: vi.fn(),
        },
      }),
    },
  };
});

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
    patch: (...args: unknown[]) => mockPatch(...args),
    delete: (...args: unknown[]) => mockDelete(...args),
  },
  extractErrorMessage: (error: unknown, fallback: string) =>
    (error as { response?: { data?: { detail?: string } } })?.response?.data
      ?.detail || fallback,
}));

vi.mock("@/lib/auth", () => ({
  useAuthStore: (selector: (state: { user: { role: string } }) => unknown) =>
    selector({ user: { role: mockCurrentRole } }),
}));

const mockMutateAccounts = vi.fn();
vi.mock("@/lib/hooks", () => ({
  useCrud: (path: string) => ({
    items:
      path === "/accounts" ? mockAccounts : path === "/roles" ? mockRoles : [],
    total: path === "/accounts" ? mockAccounts.length : 0,
    page: 1,
    loading: false,
    error: path === "/accounts" ? mockAccountsError : mockRolesError,
    setPage: vi.fn(),
    setFilter: vi.fn(),
    mutate: mockMutateAccounts,
    retry: path === "/accounts" ? mockRetryAccounts : mockRetryRoles,
  }),
}));

describe("AccountsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockCurrentRole = "admin";
    mockAccountsError = undefined;
    mockRolesError = undefined;
    mockAccounts = [
      {
        id: "acct-1",
        email: "sales@test.com",
        name: "销售账号",
        organization_id: "org-1",
        organization_name: "销售部",
        is_active: true,
        roles: [{ id: "role-operator", name: "operator" }],
      },
    ];
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: mockClipboardWriteText },
    });
    mockClipboardWriteText.mockResolvedValue(undefined);
    mockPatch.mockResolvedValue({ data: {} });
    mockDelete.mockResolvedValue({ status: 204 });
    mockPost.mockResolvedValue({ data: {} });
    mockGet.mockImplementation((url: string) => {
      if (url === "/organizations/tree") {
        return Promise.resolve({
          data: [
            { id: "org-1", name: "销售部", account_count: 1 },
            { id: "org-2", name: "市场部", account_count: 0 },
          ],
        });
      }
      return Promise.resolve({ data: [] });
    });
  });

  it("shows organization account counts and account organization names", async () => {
    render(<AccountsPage />);

    await waitFor(() => {
      expect(screen.getByTestId("org-account-count-org-1")).toHaveTextContent(
        "1"
      );
    });

    fireEvent.click(screen.getByRole("tab", { name: "账户管理" }));

    await waitFor(() => {
      expect(screen.getByTestId("account-org-name-acct-1")).toHaveTextContent(
        "销售部"
      );
      expect(screen.getByTestId("account-status-acct-1")).toHaveTextContent(
        "已启用"
      );
      expect(screen.getByText("运营人员")).toBeInTheDocument();
    });
  });

  it("keeps the current roles when editing an account", async () => {
    render(<AccountsPage />);
    await screen.findByTestId("org-account-count-org-1");
    fireEvent.click(screen.getByRole("tab", { name: "账户管理" }));
    fireEvent.mouseEnter(
      screen.getByRole("button", { name: "操作菜单-销售账号" })
    );
    fireEvent.click(await screen.findByText("编辑账户"));

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("编辑账户");
    expect(screen.getByLabelText("姓名")).toHaveAttribute(
      "id",
      "edit-account-form_name"
    );
    fireEvent.click(screen.getByRole("button", { name: /保\s*存/ }));

    await waitFor(() => {
      expect(mockPatch).toHaveBeenCalledWith("/accounts/acct-1", {
        name: "销售账号",
        organization_id: "org-1",
        role_ids: ["role-operator"],
      });
    });
  });

  it("shows the backend self-lockout reason and keeps the edit form open", async () => {
    mockAccounts = [
      {
        id: "acct-1",
        email: "admin@test.com",
        name: "当前管理员",
        organization_id: "org-1",
        organization_name: "销售部",
        is_active: true,
        roles: [{ id: "role-admin", name: "admin" }],
      },
    ];
    mockPatch.mockRejectedValueOnce({
      response: {
        data: { detail: "不能移除当前登录账户的管理员角色" },
      },
    });

    render(<AccountsPage />);
    await screen.findByTestId("org-account-count-org-1");
    fireEvent.click(screen.getByRole("tab", { name: "账户管理" }));
    fireEvent.mouseEnter(
      screen.getByRole("button", { name: "操作菜单-当前管理员" })
    );
    fireEvent.click(await screen.findByText("编辑账户"));
    fireEvent.click(screen.getByRole("button", { name: /保\s*存/ }));

    await waitFor(() => {
      expect(mockMessage.error).toHaveBeenCalledWith(
        "不能移除当前登录账户的管理员角色"
      );
    });
    expect(screen.getByRole("dialog")).toHaveTextContent("编辑账户");
    expect(mockMutateAccounts).not.toHaveBeenCalled();
  });

  it("shows directory errors, allows retry, and blocks account mutations", async () => {
    mockAccountsError = {
      response: { data: { detail: "账户目录暂时不可用" } },
    };
    mockRolesError = {
      response: { data: { detail: "角色目录暂时不可用" } },
    };

    render(<AccountsPage />);
    await screen.findByTestId("org-account-count-org-1");
    fireEvent.click(screen.getByRole("tab", { name: "账户管理" }));

    expect(screen.getByText("账户目录加载失败")).toBeInTheDocument();
    expect(screen.getByText("角色目录加载失败")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /新建账户/ })).toBeDisabled();
    expect(
      screen.queryByRole("button", { name: "操作菜单-销售账号" })
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "重新加载账户" }));
    fireEvent.click(screen.getByRole("button", { name: "重新加载角色" }));

    expect(mockRetryAccounts).toHaveBeenCalledTimes(1);
    expect(mockRetryRoles).toHaveBeenCalledTimes(1);
    expect(mockPost).not.toHaveBeenCalled();
    expect(mockPatch).not.toHaveBeenCalled();
  });

  it("requires a reason before disabling an account", async () => {
    render(<AccountsPage />);
    await screen.findByTestId("org-account-count-org-1");
    fireEvent.click(screen.getByRole("tab", { name: "账户管理" }));
    fireEvent.mouseEnter(
      screen.getByRole("button", { name: "操作菜单-销售账号" })
    );
    fireEvent.click(await screen.findByText("停用账户"));

    expect(await screen.findByText("停用「销售账号」")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("停用原因"), {
      target: { value: "员工离职" },
    });
    fireEvent.click(screen.getByRole("button", { name: "确认停用" }));

    await waitFor(() => {
      expect(mockPatch).toHaveBeenCalledWith("/accounts/acct-1/status", {
        is_active: false,
        reason: "员工离职",
      });
      expect(mockMutateAccounts).toHaveBeenCalled();
    });
  });

  it("re-enables a disabled account without a dangerous confirmation", async () => {
    mockAccounts = [
      {
        id: "acct-1",
        email: "sales@test.com",
        name: "销售账号",
        organization_id: "org-1",
        organization_name: "销售部",
        is_active: false,
        roles: [{ id: "role-operator", name: "operator" }],
      },
    ];

    render(<AccountsPage />);
    await screen.findByTestId("org-account-count-org-1");
    fireEvent.click(screen.getByRole("tab", { name: "账户管理" }));
    expect(screen.getByTestId("account-status-acct-1")).toHaveTextContent(
      "已停用"
    );
    fireEvent.mouseEnter(
      screen.getByRole("button", { name: "操作菜单-销售账号" })
    );
    fireEvent.click(await screen.findByText("重新启用"));

    await waitFor(() => {
      expect(mockPatch).toHaveBeenCalledWith("/accounts/acct-1/status", {
        is_active: true,
        reason: "管理员重新启用账户",
      });
    });
    expect(screen.queryByText("确认停用")).not.toBeInTheDocument();
  });

  it("creates an account without asking the operator to type a password", async () => {
    mockPost.mockResolvedValue({
      data: {
        id: "acct-2",
        email: "new@test.com",
        name: "新账号",
        organization_id: "org-1",
        organization_name: "销售部",
        initial_password: "Ymt-Abc123456789",
      },
    });

    render(<AccountsPage />);
    await screen.findByTestId("org-account-count-org-1");
    fireEvent.click(screen.getByRole("tab", { name: "账户管理" }));
    fireEvent.click(screen.getByRole("button", { name: /新建账户/ }));

    expect(screen.queryByLabelText("密码")).not.toBeInTheDocument();
    expect(screen.getByLabelText("姓名")).toHaveAttribute(
      "id",
      "create-account-form_name"
    );

    fireEvent.change(screen.getByLabelText("邮箱"), {
      target: { value: "new@test.com" },
    });
    fireEvent.change(screen.getByLabelText("姓名"), {
      target: { value: "新账号" },
    });
    fireEvent.click(screen.getByRole("button", { name: "创建账户" }));

    await waitFor(() => {
      expect(mockPost).toHaveBeenCalledWith("/accounts", {
        email: "new@test.com",
        name: "新账号",
        organization_id: "org-1",
      });
      expect(
        screen.getByTestId("account-initial-password-alert")
      ).toHaveTextContent("Ymt-Abc123456789");
    });

    fireEvent.click(screen.getByRole("button", { name: "复制临时密码" }));

    await waitFor(() => {
      expect(mockClipboardWriteText).toHaveBeenCalledWith("Ymt-Abc123456789");
      expect(mockMessage.success).toHaveBeenCalledWith("临时密码已复制");
    });
  });

  it("explains what accounts are for and how passwords are issued", async () => {
    render(<AccountsPage />);
    await screen.findByTestId("org-account-count-org-1");

    fireEvent.click(screen.getByRole("tab", { name: "账户管理" }));

    expect(
      screen.getByText(/账户用于员工或渠道伙伴登录后台/)
    ).toBeInTheDocument();
    expect(
      screen.getByText(/组织本身不会自动限制数据范围/)
    ).toBeInTheDocument();
    expect(
      screen.getByText(/所属组织用于归类和管理账号，不会自动限制数据范围/)
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /新建账户/ }));

    expect(screen.getByText(/不需要手动设置密码/)).toBeInTheDocument();
    expect(
      screen.getByText(/临时密码只在创建成功后显示一次/)
    ).toBeInTheDocument();
  });

  it("opens create org modal with name and parent fields", async () => {
    render(<AccountsPage />);
    await screen.findByTestId("org-account-count-org-1");

    fireEvent.click(screen.getByRole("button", { name: /新建组织/ }));

    await waitFor(() => {
      expect(screen.getByLabelText("组织名称")).toBeInTheDocument();
    });
    expect(screen.getByLabelText("组织名称")).toHaveAttribute(
      "id",
      "organization-form_name"
    );
    // TreeSelect for parent org should be present
    expect(screen.getByText("上级组织")).toBeInTheDocument();
  });

  it("shows a 500 organization failure, blocks changes, and recovers on retry", async () => {
    mockGet
      .mockRejectedValueOnce({
        response: {
          status: 500,
          data: { detail: "组织目录暂时不可用" },
        },
      })
      .mockResolvedValueOnce({
        data: [{ id: "org-1", name: "销售部", account_count: 1 }],
      });

    render(<AccountsPage />);

    expect(await screen.findByText("组织列表加载失败")).toBeInTheDocument();
    expect(screen.getByText("组织目录暂时不可用")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /新建组织/ })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "重新加载" }));

    expect(await screen.findByText("销售部")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /新建组织/ })).toBeEnabled();
    expect(mockPost).not.toHaveBeenCalled();
  });

  it("renders multiple organizations in the table", async () => {
    render(<AccountsPage />);

    await waitFor(() => {
      expect(screen.getByText("销售部")).toBeInTheDocument();
    });
    expect(screen.getByText("市场部")).toBeInTheDocument();
    expect(mockGet).toHaveBeenCalledWith("/organizations/tree");
  });

  it("renders parent-child organizations as tree data", async () => {
    mockGet.mockImplementation((url: string) => {
      if (url === "/organizations/tree") {
        return Promise.resolve({
          data: [
            {
              id: "org-parent",
              name: "总公司",
              account_count: 2,
              parent_id: null,
            },
            {
              id: "org-child",
              name: "华东销售部",
              account_count: 1,
              parent_id: "org-parent",
            },
          ],
        });
      }
      return Promise.resolve({ data: [] });
    });

    render(<AccountsPage />);

    // Root org should be visible
    await waitFor(() => {
      expect(screen.getByText("总公司")).toBeInTheDocument();
    });

    // Account counts should be rendered
    expect(
      screen.getByTestId("org-account-count-org-parent")
    ).toHaveTextContent("2");
  });

  it("shows org action menu buttons for each root row", async () => {
    render(<AccountsPage />);
    await screen.findByTestId("org-account-count-org-1");

    // Each org row should have an action dropdown button
    const menuButtons = screen.getAllByRole("button", { name: /操作菜单/ });
    expect(menuButtons.length).toBeGreaterThanOrEqual(2); // org-1 and org-2
  });

  it("gives operators a read-only account directory", async () => {
    mockCurrentRole = "operator";
    render(<AccountsPage />);
    await screen.findByTestId("org-account-count-org-1");

    expect(screen.getByText("组织用于账户归类和日常管理")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /新建组织/ })
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /操作菜单/ })
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "账户管理" }));

    expect(screen.getByText("当前为只读模式")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /新建账户/ })
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /操作菜单/ })
    ).not.toBeInTheDocument();
  });

  it("shows viewers a useful access boundary without loading directory data", () => {
    mockCurrentRole = "viewer";
    render(<AccountsPage />);

    expect(screen.getByText("当前角色不能查看账户目录")).toBeInTheDocument();
    expect(screen.getByText(/联系租户管理员处理/)).toBeInTheDocument();
    expect(mockGet).not.toHaveBeenCalled();
  });
});
