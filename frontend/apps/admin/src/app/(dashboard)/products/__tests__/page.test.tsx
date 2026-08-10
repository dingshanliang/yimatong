import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import TenantPlanReadOnly from "../../_components/TenantPlanReadOnly";
import ProductsPage from "../page";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  useCrud: vi.fn(),
  role: "admin",
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
    post: vi.fn(),
  },
  extractErrorMessage: (_error: unknown, fallback: string) => fallback,
}));

vi.mock("@/lib/hooks", () => ({
  useCrud: (...args: unknown[]) => mocks.useCrud(...args),
}));

vi.mock("@/lib/use-categories", () => ({
  useCategories: () => ({ categories: [] }),
}));

vi.mock("@/lib/auth", () => ({
  useAuthStore: (
    selector: (state: {
      user: {
        role: string;
        tenant_type: string;
        acting_tenant_id: null;
        agency_scope: null;
      };
    }) => unknown
  ) =>
    selector({
      user: {
        role: mocks.role,
        tenant_type: "brand",
        acting_tenant_id: null,
        agency_scope: null,
      },
    }),
}));

vi.mock("@/components/ImageUploadInput", () => ({
  default: () => <div data-testid="image-upload" />,
}));

vi.mock("../_components/AIDrawer", () => ({
  AIDrawer: () => null,
}));

function crudState() {
  return {
    items: [
      {
        id: "product-1",
        name: "有机大米",
        brand_id: "brand-1",
        brand_name: "青岭良仓",
        status: "active",
      },
    ],
    total: 1,
    page: 1,
    loading: false,
    error: undefined,
    setPage: vi.fn(),
    setFilter: vi.fn(),
    update: vi.fn(),
    remove: vi.fn(),
    retry: vi.fn(),
  };
}

describe("ProductsPage catalog boundaries", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.role = "admin";
    mocks.useCrud.mockReturnValue(crudState());
    mocks.get.mockResolvedValue({
      data: { items: [{ id: "brand-1", name: "青岭良仓" }] },
    });
  });

  it("does not mount data hooks or issue option requests for a viewer", () => {
    mocks.role = "viewer";

    render(<ProductsPage />);

    expect(mocks.useCrud).not.toHaveBeenCalled();
    expect(mocks.get).not.toHaveBeenCalled();
  });

  it("keeps operator write access but removes the admin-only delete action", async () => {
    mocks.role = "operator";

    render(<ProductsPage />);

    expect(await screen.findByText("有机大米")).toBeVisible();
    expect(screen.getByRole("button", { name: /新建产品/ })).toBeEnabled();
    expect(
      screen.queryByRole("button", { name: /删除/ })
    ).not.toBeInTheDocument();
  });

  it("surfaces brand option failure and disables dependent creation", async () => {
    mocks.get.mockRejectedValueOnce(new Error("network unavailable"));

    render(<ProductsPage />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /新建产品/ })).toBeDisabled();
    });
    expect(screen.getByRole("button", { name: /重\s*试/ })).toBeEnabled();
  });

  it("does not present a failed product request as an empty catalog", async () => {
    mocks.useCrud.mockReturnValue({
      ...crudState(),
      items: [],
      total: 0,
      error: new Error("catalog unavailable"),
    });

    render(<ProductsPage />);

    expect(await screen.findByRole("alert")).toBeVisible();
    expect(screen.queryByText("暂无产品")).not.toBeInTheDocument();
  });

  it("keeps list reads visible while an expired plan disables writes", async () => {
    render(
      <TenantPlanReadOnly active refreshing={false} onRefresh={vi.fn()}>
        <ProductsPage />
      </TenantPlanReadOnly>
    );

    expect(await screen.findByText("有机大米")).toBeVisible();
    expect(screen.getByRole("button", { name: /新建产品/ })).toBeDisabled();
    expect(screen.getByRole("switch")).toBeDisabled();
  });

  it("preserves typed form state when the plan becomes read-only", async () => {
    const active = (readOnly: boolean) => (
      <TenantPlanReadOnly
        active={readOnly}
        refreshing={false}
        onRefresh={vi.fn()}
      >
        <ProductsPage />
      </TenantPlanReadOnly>
    );
    const view = render(active(false));

    fireEvent.click(await screen.findByRole("button", { name: /新建产品/ }));
    const nameInput = screen.getByTestId("product-name-input");
    fireEvent.change(nameInput, { target: { value: "春耕新品" } });

    view.rerender(active(true));

    expect(screen.getByTestId("product-name-input")).toHaveValue("春耕新品");
    expect(screen.getByTestId("product-name-input")).toBeDisabled();
  });
});
