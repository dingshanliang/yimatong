import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import TenantPlanReadOnly from "../_components/TenantPlanReadOnly";
import ExportsPage from "./page";

const mocks = vi.hoisted(() => ({
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
        message: { success: vi.fn(), error: vi.fn() },
      }),
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

function crudState(items: unknown[] = []) {
  return {
    items,
    total: items.length,
    page: 1,
    loading: false,
    error: undefined,
    setPage: vi.fn(),
    mutate: vi.fn(),
    retry: vi.fn(),
  };
}

describe("ExportsPage access and delivery boundaries", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.user.role = "viewer";
    mocks.user.tenant_type = "brand";
    mocks.user.acting_tenant_id = null;
    mocks.user.agency_scope = null;
    mocks.useCrud.mockReturnValue(crudState());
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: vi.fn(() => "blob:code-export"),
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: vi.fn(),
    });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  });

  it("does not mount export data for a viewer", () => {
    render(<ExportsPage />);

    expect(screen.getByRole("alert")).toBeVisible();
    expect(mocks.useCrud).not.toHaveBeenCalled();
    expect(mocks.post).not.toHaveBeenCalled();
  });

  it("shows the current delivery status and only exports completed batches", async () => {
    mocks.user.role = "admin";
    const batches = crudState([
      {
        id: "batch-completed",
        batch_code: "CB-001",
        quantity: 2,
        expected_item_count: 4,
        code_type: "paired",
        status: "completed",
      },
      {
        id: "batch-exported",
        batch_code: "CB-002",
        quantity: 1,
        expected_item_count: 1,
        code_type: "single",
        status: "exported",
      },
    ]);
    mocks.useCrud.mockImplementation((path: string) =>
      path === "/code-batches" ? batches : crudState()
    );
    mocks.post.mockResolvedValue({
      data: new Blob(["public_id\nABC123\n"], { type: "text/csv" }),
      headers: { "content-disposition": 'attachment; filename="codes.csv"' },
    });

    render(<ExportsPage />);
    fireEvent.click(screen.getByRole("tab", { name: "码批次导出" }));

    expect(await screen.findByText("CB-001")).toBeVisible();
    expect(screen.getByText("已导出")).toBeVisible();
    expect(screen.getByText("4")).toBeVisible();
    expect(screen.getAllByRole("button", { name: /导出/ })).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: /导出/ }));
    fireEvent.change(await screen.findByLabelText("导出原因"), {
      target: { value: "  交付印刷厂  " },
    });
    fireEvent.click(screen.getByRole("button", { name: "准备导出" }));
    await vi.waitFor(() =>
      expect(mocks.post).toHaveBeenCalledWith(
        "/code-batches/batch-completed/export",
        { reason: "交付印刷厂" },
        {
          headers: { "Idempotency-Key": expect.any(String) },
          responseType: "blob",
        }
      )
    );
    expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
    expect(HTMLAnchorElement.prototype.click).toHaveBeenCalledTimes(1);
  });

  it("keeps export records readable but sends zero mutations for an expired plan", async () => {
    mocks.user.role = "admin";
    const batches = crudState([
      {
        id: "batch-completed",
        batch_code: "CB-001",
        quantity: 1,
        expected_item_count: 1,
        code_type: "single",
        status: "completed",
      },
    ]);
    mocks.useCrud.mockImplementation((path: string) =>
      path === "/code-batches" ? batches : crudState()
    );

    render(
      <TenantPlanReadOnly active refreshing={false} onRefresh={vi.fn()}>
        <ExportsPage />
      </TenantPlanReadOnly>
    );
    fireEvent.click(screen.getByRole("tab", { name: "码批次导出" }));

    expect(await screen.findByText("CB-001")).toBeVisible();
    expect(screen.getByRole("button", { name: /导出/ })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: /导出/ }));
    expect(mocks.post).not.toHaveBeenCalled();
  });
});
