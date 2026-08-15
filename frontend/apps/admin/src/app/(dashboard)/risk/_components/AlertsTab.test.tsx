import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import TenantPlanReadOnly from "../../_components/TenantPlanReadOnly";
import { AlertsTab } from "./AlertsTab";

const mocks = vi.hoisted(() => ({
  post: vi.fn(),
  useCrud: vi.fn(),
  user: {
    role: "operator",
    tenant_type: "brand",
    acting_tenant_id: null as string | null,
    agency_scope: null as string[] | null,
  },
}));

vi.mock("antd", async () => {
  const actual = await vi.importActual<typeof import("antd")>("antd");
  return {
    ...actual,
    App: {
      ...actual.App,
      useApp: () => ({ message: { success: vi.fn(), error: vi.fn() } }),
    },
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
        id: "alert-1",
        public_id: "CODE-001",
        alert_type: "risk_frozen",
        detail: "高频扫码",
        resolved: false,
      },
    ],
    total: 1,
    page: 1,
    loading: false,
    error: undefined,
    setPage: vi.fn(),
    mutate: vi.fn(),
    retry: vi.fn(),
  };
}

describe("AlertsTab permission and plan boundaries", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.user.role = "operator";
    mocks.user.tenant_type = "brand";
    mocks.user.acting_tenant_id = null;
    mocks.user.agency_scope = null;
    mocks.useCrud.mockReturnValue(crudState());
    mocks.post.mockResolvedValue({ data: {} });
  });

  it("does not mount the alert request for a viewer without risk access", () => {
    mocks.user.role = "viewer";

    render(<AlertsTab />);

    expect(screen.getByRole("alert")).toBeVisible();
    expect(mocks.useCrud).not.toHaveBeenCalled();
    expect(mocks.post).not.toHaveBeenCalled();
  });

  it("mounts the alert request for a direct brand operator with canonical risk access", async () => {
    render(<AlertsTab />);

    expect(await screen.findByText("CODE-001")).toBeVisible();
    expect(mocks.useCrud).toHaveBeenCalledWith("/risk-alerts");
  });

  it("keeps alerts readable but disables resolution for an expired plan", async () => {
    mocks.user.role = "admin";

    render(
      <TenantPlanReadOnly active refreshing={false} onRefresh={vi.fn()}>
        <AlertsTab />
      </TenantPlanReadOnly>
    );

    expect(await screen.findByText("CODE-001")).toBeVisible();
    const resolve = screen.getByRole("button", { name: "处理" });
    expect(resolve).toBeDisabled();
    fireEvent.click(resolve);
    expect(mocks.post).not.toHaveBeenCalled();
  });

  it("shows a retryable error instead of a false empty alert list", () => {
    mocks.user.role = "admin";
    const retry = vi.fn();
    mocks.useCrud.mockReturnValue({
      ...crudState(),
      items: [],
      total: 0,
      error: new Error("network"),
      retry,
    });

    render(<AlertsTab />);

    expect(screen.getByRole("alert")).toBeVisible();
    expect(screen.queryByText(/共 0 条/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /重.*试/ }));
    expect(retry).toHaveBeenCalledTimes(1);
  });
});
