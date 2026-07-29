import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import RecentEvents from "../RecentEvents";

const mockGet = vi.fn();
const mockRouterPush = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockRouterPush }),
}));

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
  },
  registerAuthInterceptorHandlers: vi.fn(),
}));

describe("RecentEvents tenant audit summary", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    const storage = new Map<string, string>();
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      value: {
        getItem: (key: string) => storage.get(key) ?? null,
        setItem: (key: string, value: string) => storage.set(key, value),
      },
    });
    window.localStorage.setItem(
      "auth_store",
      JSON.stringify({
        account_id: "account-1",
        tenant_id: "tenant-1",
        role: "admin",
        tenant_type: "brand",
        email: "admin@demo.com",
        name: "品牌管理员",
        acting_tenant_id: null,
        agency_scope: null,
      })
    );
    mockGet.mockResolvedValue({
      data: {
        items: [
          {
            id: "log-1",
            action: "page_published",
            resource: "page_template:page-1",
            timestamp: "2026-07-30T08:00:00Z",
            operator: { name: "品牌管理员" },
            details: { resource_name: "五常大米溯源页" },
          },
        ],
      },
    });
  });

  it("loads the latest five tenant operations and links to the full log", async () => {
    render(<RecentEvents />);

    expect(screen.getByText("最近操作")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "查看全部" })
    ).toBeInTheDocument();
    await waitFor(() => {
      expect(mockGet).toHaveBeenCalledWith("/audit-logs", {
        params: { page: 1, page_size: 5 },
      });
      expect(
        screen.getByText("品牌管理员 · 发布扫码页 · 五常大米溯源页")
      ).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: "查看全部" }));
    expect(mockRouterPush).toHaveBeenCalledWith("/settings/audit-logs");
  });
});
