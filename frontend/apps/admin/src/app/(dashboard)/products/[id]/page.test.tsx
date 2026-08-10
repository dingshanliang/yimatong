import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ProductWorkbenchPage from "./page";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  patch: vi.fn(),
  delete: vi.fn(),
  role: "admin",
  tenantType: "brand",
  actingTenantId: null as string | null,
  agencyScope: null as string[] | null,
  planReadOnly: false,
  batches: [] as Array<Record<string, unknown>>,
  message: {
    success: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
    warning: vi.fn(),
  },
}));

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "product-1" }),
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock("antd", async () => {
  const actual = await vi.importActual<typeof import("antd")>("antd");
  return {
    ...actual,
    App: {
      ...actual.App,
      useApp: () => ({ message: mocks.message }),
    },
  };
});

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mocks.get(...args),
    post: (...args: unknown[]) => mocks.post(...args),
    patch: (...args: unknown[]) => mocks.patch(...args),
    delete: (...args: unknown[]) => mocks.delete(...args),
  },
  extractErrorMessage: (_error: unknown, fallback: string) => fallback,
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

vi.mock("@/lib/use-categories", () => ({
  useCategories: () => ({ categories: [] }),
}));

vi.mock("../../_components/TenantPlanReadOnly", () => ({
  useTenantPlanReadOnly: () => mocks.planReadOnly,
}));

vi.mock("@/components/ImageUploadInput", () => ({
  default: () => <div data-testid="image-upload" />,
}));

vi.mock("@/components/FileUploadInput", () => ({
  default: () => <div data-testid="file-upload" />,
}));

function activeBatch(status = "active", effectiveStatus = status) {
  return {
    id: "batch-1",
    product_id: "product-1",
    sku_id: "sku-1",
    sku_name: "5kg 礼盒",
    sku_code: "RICE-5KG",
    batch_code: "PB-20260810-RICE5KG",
    production_date: "2026-08-10",
    expiry_date: "2027-08-10",
    origin: "黑龙江五常",
    status,
    effective_status: effectiveStatus,
  };
}

async function openBatchesTab() {
  expect(await screen.findByText("安心大米")).toBeVisible();
  fireEvent.click(screen.getByRole("tab", { name: "批次" }));
  expect(await screen.findByText("PB-20260810-RICE5KG")).toBeVisible();
}

describe("Product workbench batch lifecycle controls", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.role = "admin";
    mocks.tenantType = "brand";
    mocks.actingTenantId = null;
    mocks.agencyScope = null;
    mocks.planReadOnly = false;
    mocks.batches = [activeBatch()];
    mocks.post.mockResolvedValue({ data: {} });
    mocks.patch.mockResolvedValue({ data: {} });
    mocks.delete.mockResolvedValue({ data: {} });
    mocks.get.mockImplementation((url: string) => {
      if (url === "/products/product-1") {
        return Promise.resolve({
          data: {
            id: "product-1",
            name: "安心大米",
            brand_id: "brand-1",
            brand_name: "青岭良仓",
            status: "active",
          },
        });
      }
      if (url === "/products/product-1/skus") {
        return Promise.resolve({
          data: {
            items: [
              {
                id: "sku-1",
                product_id: "product-1",
                code: "RICE-5KG",
                name: "5kg 礼盒",
                status: "active",
              },
            ],
          },
        });
      }
      if (url === "/products/product-1/batches") {
        return Promise.resolve({ data: { items: mocks.batches } });
      }
      return Promise.resolve({ data: { items: [] } });
    });
  });

  it("does not mount product requests for a viewer", () => {
    mocks.role = "viewer";

    render(<ProductWorkbenchPage />);

    expect(mocks.get).not.toHaveBeenCalled();
    expect(mocks.post).not.toHaveBeenCalled();
    expect(mocks.patch).not.toHaveBeenCalled();
    expect(mocks.delete).not.toHaveBeenCalled();
  });

  it("lets an admin recall with the exact irreversible confirmation payload", async () => {
    render(<ProductWorkbenchPage />);
    await openBatchesTab();

    fireEvent.click(screen.getByRole("button", { name: "召回批次" }));
    fireEvent.change(screen.getByLabelText("召回原因"), {
      target: { value: "  抽检发现质量指标异常  " },
    });
    fireEvent.click(screen.getByRole("button", { name: "确认召回" }));

    await waitFor(() => {
      expect(mocks.post).toHaveBeenCalledWith(
        "/production-batches/batch-1/recall",
        { reason: "抽检发现质量指标异常", confirm: "recall" }
      );
    });
  }, 15_000);

  it("does not expose any recall action to an operator", async () => {
    mocks.role = "operator";

    render(<ProductWorkbenchPage />);
    await openBatchesTab();

    expect(
      screen.queryByRole("button", { name: "召回批次" })
    ).not.toBeInTheDocument();
    expect(mocks.post).not.toHaveBeenCalled();
  });

  it("lets an acting agency read product batches without any recall request", async () => {
    mocks.tenantType = "agency";
    mocks.actingTenantId = "client-1";
    mocks.agencyScope = ["products"];

    render(<ProductWorkbenchPage />);
    await openBatchesTab();

    expect(
      screen.queryByRole("button", { name: "召回批次" })
    ).not.toBeInTheDocument();
    expect(mocks.post).not.toHaveBeenCalled();
  });

  it("keeps product reads available while an expired plan makes batch mutations request-free", async () => {
    mocks.role = "operator";
    const { rerender } = render(<ProductWorkbenchPage />);
    await openBatchesTab();

    mocks.planReadOnly = true;
    rerender(<ProductWorkbenchPage />);

    const addButton = screen.getByRole("button", { name: /新增批次/ });
    const importButton = screen.getByRole("button", { name: /批量导入/ });
    const editButton = screen.getByRole("button", { name: "编辑" });
    expect(addButton).toBeDisabled();
    expect(importButton).toBeDisabled();
    expect(editButton).toBeDisabled();
    expect(
      screen.queryByRole("button", { name: "召回批次" })
    ).not.toBeInTheDocument();

    fireEvent.click(addButton);
    fireEvent.click(importButton);
    fireEvent.click(editButton);
    expect(mocks.post).not.toHaveBeenCalled();
    expect(mocks.patch).not.toHaveBeenCalled();
    expect(mocks.delete).not.toHaveBeenCalled();
  }, 15_000);

  it.each([
    { persistedStatus: "recalled", effectiveStatus: "recalled" },
    { persistedStatus: "active", effectiveStatus: "expired" },
  ])(
    "keeps $effectiveStatus batches visible but makes them non-editable",
    async ({ persistedStatus, effectiveStatus }) => {
      mocks.batches = [activeBatch(persistedStatus, effectiveStatus)];

      render(<ProductWorkbenchPage />);
      await openBatchesTab();

      expect(
        screen.getByText(effectiveStatus === "recalled" ? "已召回" : "已过期")
      ).toBeVisible();
      expect(screen.getByRole("button", { name: "编辑" })).toBeDisabled();
      expect(
        screen.queryByRole("button", { name: "召回批次" })
      ).not.toBeInTheDocument();
    },
    15_000
  );
});
