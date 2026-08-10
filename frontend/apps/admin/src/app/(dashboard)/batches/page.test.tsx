import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import TenantPlanReadOnly from "../_components/TenantPlanReadOnly";
import BatchesPage from "./page";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  useCrud: vi.fn(),
  update: vi.fn(),
  user: {
    role: "admin",
    tenant_type: "brand",
    acting_tenant_id: null as string | null,
    agency_scope: null as string[] | null,
  },
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
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
  default: {
    get: (...args: unknown[]) => mocks.get(...args),
    post: (...args: unknown[]) => mocks.post(...args),
  },
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
        id: "batch-1",
        product_id: "product-1",
        product_name: "安心大米",
        sku_id: "sku-1",
        sku_name: "5kg 礼盒",
        sku_code: "RICE-5KG",
        batch_code: "PB-20260810-RICE5KG",
        production_date: "2026-08-10",
        expiry_date: "2027-08-10",
        origin: "黑龙江五常",
        status: "active",
        effective_status: "active",
      },
    ],
    total: 1,
    page: 1,
    loading: false,
    error: undefined,
    setPage: vi.fn(),
    setFilter: vi.fn(),
    create: vi.fn(),
    update: mocks.update,
    retry: vi.fn(),
  };
}

describe("BatchesPage catalog and lifecycle boundaries", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.user.role = "admin";
    mocks.user.tenant_type = "brand";
    mocks.user.acting_tenant_id = null;
    mocks.user.agency_scope = null;
    mocks.useCrud.mockReturnValue(crudState());
    mocks.get.mockResolvedValue({
      data: { items: [{ id: "product-1", name: "安心大米" }] },
    });
    mocks.post.mockResolvedValue({ data: {} });
  });

  it("does not mount catalog hooks or option requests for a viewer", () => {
    mocks.user.role = "viewer";

    render(<BatchesPage />);

    expect(mocks.useCrud).not.toHaveBeenCalled();
    expect(mocks.get).not.toHaveBeenCalled();
  });

  it("allows an operator to maintain batches without exposing recall", async () => {
    mocks.user.role = "operator";

    render(<BatchesPage />);

    expect(await screen.findByText("PB-20260810-RICE5KG")).toBeVisible();
    expect(screen.getByRole("button", { name: /新建批次/ })).toBeEnabled();
    expect(
      screen.queryByRole("button", { name: /召回批次/ })
    ).not.toBeInTheDocument();
    expect(mocks.post).not.toHaveBeenCalled();
  });

  it("lets an acting agency read batches without exposing or requesting recall", async () => {
    mocks.user.role = "admin";
    mocks.user.tenant_type = "agency";
    mocks.user.acting_tenant_id = "client-1";
    mocks.user.agency_scope = ["products"];

    render(<BatchesPage />);

    expect(await screen.findByText("PB-20260810-RICE5KG")).toBeVisible();
    expect(
      screen.queryByRole("button", { name: /召回批次/ })
    ).not.toBeInTheDocument();
    expect(mocks.post).not.toHaveBeenCalled();
  });

  it.each([
    { persistedStatus: "recalled", effectiveStatus: "recalled" },
    { persistedStatus: "active", effectiveStatus: "expired" },
  ])(
    "keeps $effectiveStatus batches readable but blocks editing and recall",
    async ({ persistedStatus, effectiveStatus }) => {
      const state = crudState();
      mocks.useCrud.mockReturnValue({
        ...state,
        items: [
          {
            ...state.items[0],
            status: persistedStatus,
            effective_status: effectiveStatus,
          },
        ],
      });

      render(<BatchesPage />);

      expect(await screen.findByText("PB-20260810-RICE5KG")).toBeVisible();
      expect(
        screen.getByText(effectiveStatus === "recalled" ? "已召回" : "已过期")
      ).toBeVisible();
      expect(screen.getByRole("button", { name: "编辑" })).toBeDisabled();
      expect(
        screen.queryByRole("button", { name: "召回批次" })
      ).not.toBeInTheDocument();
    }
  );

  it("submits the exact admin-only recall confirmation contract", async () => {
    render(<BatchesPage />);

    fireEvent.click(await screen.findByRole("button", { name: /召回批次/ }));
    fireEvent.change(screen.getByLabelText("召回原因"), {
      target: { value: "  例行抽检发现指标异常  " },
    });
    fireEvent.click(screen.getByRole("button", { name: "确认召回" }));

    await waitFor(() => {
      expect(mocks.post).toHaveBeenCalledWith(
        "/production-batches/batch-1/recall",
        { reason: "例行抽检发现指标异常", confirm: "recall" }
      );
    });
  });

  it("keeps reads visible while an expired plan prevents every lifecycle mutation", async () => {
    render(
      <TenantPlanReadOnly active refreshing={false} onRefresh={vi.fn()}>
        <BatchesPage />
      </TenantPlanReadOnly>
    );

    expect(await screen.findByText("PB-20260810-RICE5KG")).toBeVisible();
    expect(screen.getByRole("button", { name: /新建批次/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: "编辑" })).toBeDisabled();
    expect(screen.getByRole("button", { name: /召回批次/ })).toBeDisabled();
    expect(mocks.post).not.toHaveBeenCalled();
    expect(mocks.update).not.toHaveBeenCalled();
  });

  it("separates list failure from an empty batch catalog", async () => {
    mocks.useCrud.mockReturnValue({
      ...crudState(),
      items: [],
      total: 0,
      error: new Error("catalog unavailable"),
    });

    render(<BatchesPage />);

    expect(await screen.findByRole("alert")).toBeVisible();
    expect(screen.queryByText("暂无生产批次")).not.toBeInTheDocument();
  });

  it("surfaces product option failure and disables dependent creation", async () => {
    mocks.get.mockRejectedValueOnce(new Error("network unavailable"));

    render(<BatchesPage />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /新建批次/ })).toBeDisabled();
    });
    expect(screen.getByRole("button", { name: /重\s*试/ })).toBeEnabled();
  });

  it("separates an empty product dependency from loading and failure", async () => {
    mocks.get.mockResolvedValueOnce({ data: { items: [] } });

    render(<BatchesPage />);

    expect(
      await screen.findByText(/暂无可用产品，请先创建产品和 SKU/)
    ).toBeVisible();
    expect(screen.getByRole("button", { name: /新建批次/ })).toBeDisabled();
  });
});
