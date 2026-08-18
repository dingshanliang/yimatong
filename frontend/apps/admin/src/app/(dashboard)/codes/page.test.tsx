import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import TenantPlanReadOnly from "../_components/TenantPlanReadOnly";
import CodesPage from "./page";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  useCrud: vi.fn(),
  user: {
    role: "viewer",
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
      useApp: () => ({
        message: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
        modal: { confirm: vi.fn() },
      }),
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
        id: "code-batch-1",
        batch_code: "CB-20260811-001",
        quantity: 100,
        expected_item_count: 100,
        product_id: "product-1",
        product_name: "安心大米",
        sku_id: "sku-1",
        sku_name: "5kg 礼盒",
        sku_code: "RICE-5KG",
        production_batch_id: "production-batch-1",
        production_batch_code: "PB-20260811-001",
        production_date: "2026-08-11",
        code_type: "single",
        generation_mode: "item_level",
        status: "completed",
      },
    ],
    total: 1,
    page: 1,
    loading: false,
    error: undefined,
    setPage: vi.fn(),
    setFilter: vi.fn(),
    mutate: vi.fn(),
    retry: vi.fn(),
  };
}

describe("CodesPage access and delivery boundaries", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.user.role = "viewer";
    mocks.user.tenant_type = "brand";
    mocks.user.acting_tenant_id = null;
    mocks.user.agency_scope = null;
    mocks.useCrud.mockReturnValue(crudState());
    mocks.get.mockResolvedValue({ data: { items: [] } });
  });

  it("keeps the safe batch catalog readable for viewers without mounting mutation dependencies", async () => {
    render(<CodesPage />);

    expect(await screen.findByText("CB-20260811-001")).toBeVisible();
    expect(screen.getByRole("link", { name: "详情" })).toHaveAttribute(
      "href",
      "/codes/code-batch-1"
    );
    expect(mocks.get).not.toHaveBeenCalled();
    expect(
      screen.queryByRole("button", { name: /生成码批次/ })
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /导出码表/ })
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /激活码批次/ })
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /标记印刷中/ })
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("searchbox", { name: "公开码或码号" })
    ).not.toBeInTheDocument();
    expect(mocks.post).not.toHaveBeenCalled();
  });

  it("queries an exact public code and links the operator to its owning batch", async () => {
    mocks.user.role = "admin";
    mocks.get.mockImplementation(async (path: string) => {
      if (path === "/code-items") {
        return {
          data: {
            items: [
              {
                id: "code-item-1",
                code_batch_id: "code-batch-1",
                public_id: "CODE-001",
                status: "frozen",
                code_type: "single",
              },
            ],
            total: 1,
          },
        };
      }
      return { data: { items: [] } };
    });

    render(<CodesPage />);

    fireEvent.change(screen.getByRole("searchbox", { name: "公开码或码号" }), {
      target: { value: "  CODE-001  " },
    });
    fireEvent.click(screen.getByRole("button", { name: /查\s*询/ }));

    await vi.waitFor(() =>
      expect(mocks.get).toHaveBeenCalledWith("/code-items", {
        params: {
          public_id: "CODE-001",
          page: 1,
          page_size: 20,
        },
      })
    );
    expect(await screen.findByText("CODE-001")).toBeVisible();
    expect(screen.getByText("已冻结")).toBeVisible();
    expect(
      screen.getByRole("link", { name: "查看所属码批次" })
    ).toHaveAttribute("href", "/codes/code-batch-1");
  });

  it("switches to public-code prefix lookup without sending the exact filter", async () => {
    mocks.user.role = "operator";
    mocks.get.mockResolvedValue({ data: { items: [], total: 0 } });

    render(<CodesPage />);

    fireEvent.mouseDown(screen.getByRole("combobox", { name: "查询方式" }));
    fireEvent.click(await screen.findByText("前缀查询"));
    fireEvent.change(screen.getByRole("searchbox", { name: "公开码或码号" }), {
      target: { value: "CODE" },
    });
    fireEvent.click(screen.getByRole("button", { name: /查\s*询/ }));

    await vi.waitFor(() =>
      expect(mocks.get).toHaveBeenCalledWith("/code-items", {
        params: {
          public_id_prefix: "CODE",
          page: 1,
          page_size: 20,
        },
      })
    );
    expect(await screen.findByText("未找到当前租户下的匹配码")).toBeVisible();
  });

  it("keeps the catalog readable while an expired plan disables every code mutation", async () => {
    mocks.user.role = "admin";
    mocks.useCrud.mockReturnValue({
      ...crudState(),
      items: [{ ...crudState().items[0], status: "exported" }],
    });

    render(
      <TenantPlanReadOnly active refreshing={false} onRefresh={vi.fn()}>
        <CodesPage />
      </TenantPlanReadOnly>
    );

    expect(await screen.findByText("CB-20260811-001")).toBeVisible();
    expect(screen.getByRole("button", { name: /生成码批次/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /导出码表/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /标记印刷中/ })).toBeDisabled();
    expect(mocks.post).not.toHaveBeenCalled();
  });

  it("does not expose delivery shortcuts before the completed batch is exported", async () => {
    mocks.user.role = "admin";

    render(<CodesPage />);

    expect(await screen.findByText("CB-20260811-001")).toBeVisible();
    expect(screen.getByRole("button", { name: /导出码表/ })).toBeEnabled();
    expect(
      screen.queryByRole("button", { name: /标记印刷中/ })
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /激活码批次/ })
    ).not.toBeInTheDocument();
  });

  it("imports an exact CSV into an imported-source staging batch", async () => {
    mocks.user.role = "operator";
    const mutate = vi.fn();
    mocks.useCrud.mockReturnValue({
      ...crudState(),
      mutate,
      items: [
        {
          ...crudState().items[0],
          source: "imported",
          status: "generating",
          expected_item_count: 2,
        },
      ],
    });
    mocks.post.mockResolvedValue({
      data: { imported: 2, skipped: 0, failed: 0 },
    });

    const { container } = render(<CodesPage />);

    expect(
      await screen.findByRole("button", { name: /导入既有码/ })
    ).toBeEnabled();
    const input = container.querySelector<HTMLInputElement>(
      'input[type="file"][accept=".csv,text/csv"]'
    );
    expect(input).not.toBeNull();
    const file = new File(["public_id\nABC123\nDEF456\n"], "codes.csv", {
      type: "text/csv",
    });
    fireEvent.change(input!, { target: { files: [file] } });

    await vi.waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(1));
    const [path, body, config] = mocks.post.mock.calls[0]!;
    expect(path).toBe("/imports/existing-codes");
    expect(body).toBeInstanceOf(FormData);
    expect((body as FormData).get("file")).toBe(file);
    expect(config).toEqual({ params: { code_batch_id: "code-batch-1" } });
    expect(mutate).toHaveBeenCalledTimes(1);
  });

  it("reuses one idempotency key when the same create attempt is retried", async () => {
    mocks.user.role = "admin";
    const idempotencyKey = "11111111-1111-4111-8111-111111111111";
    vi.spyOn(crypto, "randomUUID").mockReturnValue(idempotencyKey);
    mocks.get.mockImplementation(async (path: string) => {
      if (path === "/products") {
        return { data: { items: [{ id: "product-1", name: "安心大米" }] } };
      }
      if (path === "/skus") {
        return {
          data: {
            items: [{ id: "sku-1", product_id: "product-1", name: "5kg 礼盒" }],
          },
        };
      }
      return {
        data: {
          items: [
            {
              id: "production-batch-1",
              batch_code: "PB-20260811-001",
              product_id: "product-1",
              sku_id: "sku-1",
              production_date: "2026-08-11",
              expiry_date: "2027-08-11",
              status: "active",
              effective_status: "active",
            },
            {
              id: "production-batch-expired",
              batch_code: "PB-EXPIRED",
              product_id: "product-1",
              sku_id: "sku-1",
              production_date: "2025-08-11",
              expiry_date: "2026-08-10",
              status: "active",
              effective_status: "expired",
            },
          ],
        },
      };
    });
    mocks.post
      .mockRejectedValueOnce(new Error("temporary"))
      .mockResolvedValueOnce({ data: { id: "code-batch-2" } });

    render(<CodesPage />);

    fireEvent.click(await screen.findByRole("button", { name: /生成码批次/ }));
    fireEvent.mouseDown(
      screen.getByTestId("code-batch-product-select").querySelector("input")!
    );
    const productLabels = await screen.findAllByText("安心大米");
    fireEvent.click(productLabels.at(-1)!);
    await vi.waitFor(() =>
      expect(mocks.get).toHaveBeenCalledWith("/skus", {
        params: { product_id: "product-1", page_size: 100 },
      })
    );
    const skuSelect = screen.getByTestId("code-batch-sku-select");
    await vi.waitFor(() =>
      expect(skuSelect).not.toHaveClass("ant-select-disabled")
    );
    fireEvent.mouseDown(skuSelect);
    const skuLabels = await screen.findAllByText("5kg 礼盒");
    fireEvent.click(skuLabels.at(-1)!);
    await vi.waitFor(() =>
      expect(mocks.get).toHaveBeenCalledWith("/production-batches", {
        params: {
          product_id: "product-1",
          sku_id: "sku-1",
          page_size: 100,
        },
      })
    );
    const productionBatchSelect = screen.getByTestId(
      "code-batch-production-batch-select"
    );
    await vi.waitFor(() =>
      expect(productionBatchSelect).not.toHaveClass("ant-select-disabled")
    );
    fireEvent.mouseDown(productionBatchSelect);
    expect(
      screen.queryByRole("option", { name: /PB-EXPIRED/ })
    ).not.toBeInTheDocument();
    const productionBatchLabels = await screen.findAllByText(/PB-20260811-001/);
    fireEvent.click(productionBatchLabels.at(-1)!);
    fireEvent.change(screen.getByTestId("code-batch-quantity-input"), {
      target: { value: "2" },
    });

    const dialog = screen.getByRole("dialog");
    const submit = within(dialog).getByRole("button", { name: "生成码批次" });
    fireEvent.click(submit);
    await vi.waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(1));
    fireEvent.click(submit);
    await vi.waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(2));

    expect(mocks.post.mock.calls[0]?.[2]).toEqual({
      headers: { "Idempotency-Key": idempotencyKey },
    });
    expect(mocks.post.mock.calls[1]?.[2]).toEqual({
      headers: { "Idempotency-Key": idempotencyKey },
    });
    expect(crypto.randomUUID).toHaveBeenCalledTimes(1);
  }, 15_000);

  it("shows a retryable list failure instead of a false empty catalog", async () => {
    const retry = vi.fn();
    mocks.useCrud.mockReturnValue({
      ...crudState(),
      items: [],
      total: 0,
      error: new Error("permission denied"),
      retry,
    });

    render(<CodesPage />);

    const alert = await screen.findByRole("alert");
    expect(alert).toBeVisible();
    expect(
      screen.queryByText("暂无码批次，请先生成码批次")
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /重.*试/ }));
    expect(retry).toHaveBeenCalledTimes(1);
  });
});
