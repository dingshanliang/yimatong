import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AccountsPage from "../page";

const mockMessage = { success: vi.fn(), error: vi.fn() };
const mockGet = vi.fn();
const mockPost = vi.fn();
const mockPatch = vi.fn();
const mockDelete = vi.fn();
const mockClipboardWriteText = vi.fn();

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
}));

const mockMutateAccounts = vi.fn();
vi.mock("@/lib/hooks", () => ({
  useCrud: () => ({
    items: [
      { id: "acct-1", email: "sales@test.com", name: "销售账号", organization_id: "org-1", organization_name: "销售部" },
    ],
    total: 1,
    page: 1,
    loading: false,
    setPage: vi.fn(),
    setFilter: vi.fn(),
    mutate: mockMutateAccounts,
  }),
}));

describe("AccountsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: mockClipboardWriteText },
    });
    mockClipboardWriteText.mockResolvedValue(undefined);
    mockPatch.mockResolvedValue({ data: {} });
    mockDelete.mockResolvedValue({ status: 204 });
    mockPost.mockResolvedValue({ data: {} });
    mockGet.mockImplementation((url: string) => {
      if (url === "/organizations") {
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
      expect(screen.getByTestId("org-account-count-org-1")).toHaveTextContent("1");
    });

    fireEvent.click(screen.getByRole("tab", { name: "账户管理" }));

    await waitFor(() => {
      expect(screen.getByTestId("account-org-name-acct-1")).toHaveTextContent("销售部");
    });
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

    fireEvent.change(screen.getByLabelText("邮箱"), { target: { value: "new@test.com" } });
    fireEvent.change(screen.getByLabelText("姓名"), { target: { value: "新账号" } });
    fireEvent.click(screen.getByRole("button", { name: "创建账户" }));

    await waitFor(() => {
      expect(mockPost).toHaveBeenCalledWith("/accounts", {
        email: "new@test.com",
        name: "新账号",
        organization_id: "org-1",
      });
      expect(screen.getByTestId("account-initial-password-alert")).toHaveTextContent("Ymt-Abc123456789");
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

    expect(screen.getByText(/账户用于员工或渠道伙伴登录后台/)).toBeInTheDocument();
    expect(screen.getByText(/所属组织决定账号可查看和操作的数据范围/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /新建账户/ }));

    expect(screen.getByText(/不需要手动设置密码/)).toBeInTheDocument();
    expect(screen.getByText(/临时密码只在创建成功后显示一次/)).toBeInTheDocument();
  });

  it("opens create org modal with name and parent fields", async () => {
    render(<AccountsPage />);
    await screen.findByTestId("org-account-count-org-1");

    fireEvent.click(screen.getByRole("button", { name: /新建组织/ }));

    await waitFor(() => {
      expect(screen.getByLabelText("组织名称")).toBeInTheDocument();
    });
    // TreeSelect for parent org should be present
    expect(screen.getByText("上级组织")).toBeInTheDocument();
  });

  it("renders multiple organizations in the table", async () => {
    render(<AccountsPage />);

    await waitFor(() => {
      expect(screen.getByText("销售部")).toBeInTheDocument();
    });
    expect(screen.getByText("市场部")).toBeInTheDocument();
  });

  it("renders parent-child organizations as tree data", async () => {
    mockGet.mockImplementation((url: string) => {
      if (url === "/organizations") {
        return Promise.resolve({
          data: [
            { id: "org-parent", name: "总公司", account_count: 2, parent_id: null },
            { id: "org-child", name: "华东销售部", account_count: 1, parent_id: "org-parent" },
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
    expect(screen.getByTestId("org-account-count-org-parent")).toHaveTextContent("2");
  });

  it("shows org action menu buttons for each root row", async () => {
    render(<AccountsPage />);
    await screen.findByTestId("org-account-count-org-1");

    // Each org row should have an action dropdown button
    const menuButtons = screen.getAllByRole("button", { name: /操作菜单/ });
    expect(menuButtons.length).toBeGreaterThanOrEqual(2); // org-1 and org-2
  });
});
