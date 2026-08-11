import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ConnectorsPage from "./page";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  role: "viewer",
  tenantType: "brand",
  actingTenantId: null as string | null,
  agencyScope: null as string[] | null,
  message: { error: vi.fn() },
}));

vi.mock("antd", async () => {
  const actual = await vi.importActual<typeof import("antd")>("antd");
  return {
    ...actual,
    App: { ...actual.App, useApp: () => ({ message: mocks.message }) },
  };
});

vi.mock("@/lib/api", () => ({
  default: { get: (...args: unknown[]) => mocks.get(...args) },
}));

vi.mock("@/lib/auth", () => ({
  useAuthStore: (
    selector: (state: {
      user: {
        role: string;
        tenant_type: string;
        acting_tenant_id: string | null;
        agency_scope: string[] | null;
      };
    }) => unknown
  ) =>
    selector({
      user: {
        role: mocks.role,
        tenant_type: mocks.tenantType,
        acting_tenant_id: mocks.actingTenantId,
        agency_scope: mocks.agencyScope,
      },
    }),
}));

vi.mock("./_components/ConnectorsTab", () => ({
  ConnectorsTab: (props: {
    page: number;
    total: number;
    onPageChange: (page: number) => void;
  }) => (
    <div>
      连接器列表 第{props.page}页 共{props.total}条
      <button onClick={() => props.onPageChange(2)}>下一页</button>
    </div>
  ),
}));
vi.mock("./_components/CouponPoolsTab", () => ({
  CouponPoolsTab: () => <div>券码池列表</div>,
}));
vi.mock("./_components/DeliveriesTab", () => ({
  DeliveriesTab: () => <div>发放记录列表</div>,
}));

describe("ConnectorsPage access fence", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.role = "viewer";
    mocks.tenantType = "brand";
    mocks.actingTenantId = null;
    mocks.agencyScope = null;
    mocks.get.mockImplementation((url: string) =>
      Promise.resolve(
        url.endsWith("/types")
          ? { data: { types: ["generic_http"] } }
          : {
              data: {
                items: [{ id: "connector-1", enabled: true }],
                total: 21,
              },
            }
      )
    );
  });

  it("renders the denied state without issuing requests", () => {
    render(<ConnectorsPage />);

    expect(screen.getByText("当前账号无权查看连接器与券码池")).toBeVisible();
    expect(mocks.get).not.toHaveBeenCalled();
  });

  it("loads connector authority for an acting campaigns administrator", async () => {
    mocks.role = "admin";
    mocks.tenantType = "agency";
    mocks.actingTenantId = "client-tenant";
    mocks.agencyScope = ["campaigns"];

    render(<ConnectorsPage />);

    await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(2));
    expect(mocks.get).toHaveBeenCalledWith("/connectors/connectors", {
      params: { page: 1, page_size: 20 },
    });
    expect(mocks.get).toHaveBeenCalledWith("/connectors/connectors/types");
    expect(screen.getByText(/第1页 共21条/)).toBeVisible();
  });

  it("requests the selected server page", async () => {
    mocks.role = "admin";

    render(<ConnectorsPage />);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "下一页" })).toBeVisible()
    );
    screen.getByRole("button", { name: "下一页" }).click();

    await waitFor(() =>
      expect(mocks.get).toHaveBeenCalledWith("/connectors/connectors", {
        params: { page: 2, page_size: 20 },
      })
    );
  });
});
