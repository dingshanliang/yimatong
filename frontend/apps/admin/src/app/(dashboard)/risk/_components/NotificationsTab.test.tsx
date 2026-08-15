import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { NotificationsTab } from "./NotificationsTab";

const mocks = vi.hoisted(() => ({
  post: vi.fn(),
  useCrud: vi.fn(),
  user: {
    role: "operator",
    tenant_type: "brand",
    acting_tenant_id: null as string | null,
  },
}));

vi.mock("antd", async () => {
  const actual = await vi.importActual<typeof import("antd")>("antd");
  return {
    ...actual,
    message: { success: vi.fn() },
  };
});

vi.mock("@/lib/api", () => ({
  default: { post: (...args: unknown[]) => mocks.post(...args) },
}));

vi.mock("@/lib/hooks", () => ({
  useCrud: (...args: unknown[]) => mocks.useCrud(...args),
}));

vi.mock("@/lib/auth", () => ({
  useAuthStore: (selector: (state: { user: typeof mocks.user }) => unknown) =>
    selector({ user: mocks.user }),
}));

function crudState() {
  return {
    items: [
      {
        id: "notification-1",
        notification_type: "risk_warn",
        title: "扫码频率异常",
        detail: "同一来源短时间内多次扫码",
        read: false,
      },
    ],
    total: 1,
    page: 1,
    loading: false,
    setPage: vi.fn(),
    mutate: vi.fn(),
  };
}

describe("NotificationsTab risk access and idempotency", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.user.role = "operator";
    mocks.user.tenant_type = "brand";
    mocks.user.acting_tenant_id = null;
    mocks.useCrud.mockReturnValue(crudState());
    mocks.post.mockResolvedValue({ data: {} });
  });

  it("does not mount or request notifications for a viewer", () => {
    mocks.user.role = "viewer";

    render(<NotificationsTab />);

    expect(screen.getByRole("alert")).toBeVisible();
    expect(mocks.useCrud).not.toHaveBeenCalled();
    expect(mocks.post).not.toHaveBeenCalled();
  });

  it("allows a direct brand operator to read and mark one notification", async () => {
    render(<NotificationsTab />);

    expect(await screen.findByText("扫码频率异常")).toBeVisible();
    expect(mocks.useCrud).toHaveBeenCalledWith("/risk-notifications");
    fireEvent.click(screen.getByText("扫码频率异常"));

    await waitFor(() =>
      expect(mocks.post).toHaveBeenCalledWith(
        "/risk-notifications/notification-1/read",
        undefined,
        expect.objectContaining({
          headers: { "Idempotency-Key": expect.any(String) },
        })
      )
    );
  });

  it("sends an idempotency key when an operator marks all notifications read", async () => {
    render(<NotificationsTab />);

    fireEvent.click(await screen.findByRole("button", { name: "全部已读" }));

    await waitFor(() =>
      expect(mocks.post).toHaveBeenCalledWith(
        "/risk-notifications/mark-all-read",
        undefined,
        expect.objectContaining({
          headers: { "Idempotency-Key": expect.any(String) },
        })
      )
    );
  });
});
