import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AccountsPage from "../page";

const mockMessage = { success: vi.fn(), error: vi.fn() };
const mockGet = vi.fn();
const mockPost = vi.fn();

vi.mock("antd", async () => {
  const actual = await vi.importActual<typeof import("antd")>("antd");
  return {
    ...actual,
    App: {
      ...actual.App,
      useApp: () => ({ message: mockMessage }),
    },
  };
});

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
  },
}));

describe("AccountsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGet.mockImplementation((url: string) => {
      if (url === "/organizations") {
        return Promise.resolve({
          data: [{ id: "org-1", name: "销售部", account_count: 1 }],
        });
      }
      if (url === "/accounts") {
        return Promise.resolve({
          data: [{ id: "acct-1", email: "sales@test.com", name: "销售账号", organization_id: "org-1", organization_name: "销售部" }],
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
  });
});
