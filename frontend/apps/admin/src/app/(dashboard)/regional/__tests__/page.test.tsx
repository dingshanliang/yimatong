import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import RegionalPage from "../page";

const { mockMessage } = vi.hoisted(() => ({
  mockMessage: { success: vi.fn(), error: vi.fn() },
}));
const mockGet = vi.fn();
const mockPost = vi.fn();
let mockUser: {
  tenant_type: string;
  role: string;
  acting_tenant_id: string | null;
} | null = { tenant_type: "brand", role: "admin", acting_tenant_id: null };

vi.mock("antd", async () => {
  const actual = await vi.importActual<typeof import("antd")>("antd");
  return {
    ...actual,
    message: mockMessage,
  };
});

vi.mock("@/lib/auth", () => ({
  useAuthStore: (selector: (state: { user: typeof mockUser }) => unknown) =>
    selector({ user: mockUser }),
}));

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
  },
  extractErrorMessage: (_err: unknown, fallback = "操作失败") => fallback,
}));

function mockOrgsSuccess() {
  mockGet.mockImplementation((url: string) => {
    if (url === "/regional/orgs") {
      return Promise.resolve({
        data: [
          {
            id: "regional-1",
            name: "赣南脐橙协会",
            org_type: "association",
            org_type_label: "协会组织",
            member_count: 2,
            active_member_count: 1,
            template_count: 1,
            next_action: "配置统一活动",
          },
        ],
      });
    }
    if (String(url).startsWith("/regional/orgs/regional-1/members")) {
      return Promise.resolve({ data: { items: [], total: 0 } });
    }
    if (url === "/tenants") {
      return Promise.resolve({
        data: { items: [{ id: "tenant-1", name: "成员企业A" }] },
      });
    }
    return Promise.resolve({ data: [] });
  });
}

describe("RegionalPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUser = { tenant_type: "brand", role: "admin", acting_tenant_id: null };
    mockOrgsSuccess();
  });

  it("shows regional organization operating summary before drilling into tabs", async () => {
    render(<RegionalPage />);

    await waitFor(() => {
      expect(
        screen.getByTestId("regional-org-summary-regional-1")
      ).toHaveTextContent("1/2");
      expect(
        screen.getByTestId("regional-org-next-action-regional-1")
      ).toHaveTextContent("配置统一活动");
    });
  });

  it("adds a member by selecting a client instead of typing a tenant id", async () => {
    mockPost.mockResolvedValue({ data: { id: "member-1" } });
    render(<RegionalPage />);

    await screen.findByTestId("regional-org-summary-regional-1");
    fireEvent.click(screen.getByText("赣南脐橙协会"));
    fireEvent.click(await screen.findByRole("button", { name: /添加成员/ }));

    await waitFor(() => {
      expect(screen.getByTestId("member-client-select")).toBeInTheDocument();
      expect(screen.queryByLabelText("成员租户 ID")).not.toBeInTheDocument();
    });
  });

  it("uses a theme-aware selected row background for regional organizations", async () => {
    render(<RegionalPage />);

    await screen.findByTestId("regional-org-summary-regional-1");
    fireEvent.click(screen.getByText("赣南脐橙协会"));

    const selectedRow = screen.getByText("赣南脐橙协会").closest("tr");

    expect(selectedRow).toHaveStyle({
      background: "var(--admin-table-row-selected-bg)",
    });
  });

  it("explains what shared templates are used for", async () => {
    render(<RegionalPage />);

    await screen.findByTestId("regional-org-summary-regional-1");
    fireEvent.click(screen.getByText("赣南脐橙协会"));
    fireEvent.click(screen.getByRole("tab", { name: "共享模板" }));

    expect(
      screen.getByText(/统一沉淀区域品牌的页面或活动配置/)
    ).toBeInTheDocument();
    expect(screen.getByText(/下发给成员企业复用/)).toBeInTheDocument();
  });

  it("denies viewers before any request like the backend channel:read gate", async () => {
    mockUser = { tenant_type: "brand", role: "viewer", acting_tenant_id: null };
    render(<RegionalPage />);

    expect(screen.getByText("当前账号无区域组织管理权限")).toBeInTheDocument();
    expect(mockGet).not.toHaveBeenCalled();
  });

  it("shows the create action only for roles that hold channel:manage", async () => {
    const { rerender } = render(<RegionalPage />);
    expect(
      await screen.findByTestId("regional-org-summary-regional-1")
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /新建组织/ })
    ).toBeInTheDocument();

    mockUser = {
      tenant_type: "brand",
      role: "operator",
      acting_tenant_id: null,
    };
    rerender(<RegionalPage />);
    expect(
      screen.getByRole("button", { name: /新建组织/ })
    ).toBeInTheDocument();

    mockUser = { tenant_type: "brand", role: "viewer", acting_tenant_id: null };
    rerender(<RegionalPage />);
    expect(
      await screen.findByText("当前账号无区域组织管理权限")
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /新建组织/ })
    ).not.toBeInTheDocument();
  });

  it("surfaces list load failures with an error alert and a working retry", async () => {
    mockGet.mockImplementation((url: string) => {
      if (url === "/regional/orgs") {
        return Promise.reject(new Error("network down"));
      }
      return Promise.resolve({ data: [] });
    });
    render(<RegionalPage />);

    expect(await screen.findByText("区域组织加载失败")).toBeInTheDocument();
    expect(mockMessage.error).toHaveBeenCalledWith("加载区域组织失败");

    mockOrgsSuccess();
    fireEvent.click(screen.getByRole("button", { name: /重\s*试/ }));

    await waitFor(() => {
      expect(
        screen.getByTestId("regional-org-summary-regional-1")
      ).toHaveTextContent("1/2");
    });
    expect(screen.queryByText("区域组织加载失败")).not.toBeInTheDocument();
  });

  it("reports organization creation failures instead of failing silently", async () => {
    mockPost.mockRejectedValue(new Error("boom"));
    render(<RegionalPage />);
    await screen.findByTestId("regional-org-summary-regional-1");

    fireEvent.click(screen.getByRole("button", { name: /新建组织/ }));
    fireEvent.change(screen.getByLabelText("组织名称"), {
      target: { value: "测试协会" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^(OK|确\s?定)$/ }));

    await waitFor(() => {
      expect(mockPost).toHaveBeenCalledWith("/regional/orgs", {
        name: "测试协会",
        org_type: "association",
      });
      expect(mockMessage.error).toHaveBeenCalledWith("创建区域组织失败");
    });
  });
});
