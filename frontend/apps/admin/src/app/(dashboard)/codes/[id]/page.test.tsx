import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import CodeBatchDetailPage from "./page";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  push: vi.fn(),
  user: {
    role: "admin",
    tenant_type: "brand",
    acting_tenant_id: null as string | null,
    agency_scope: null as string[] | null,
  },
}));

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "code-batch-1" }),
  useRouter: () => ({ push: mocks.push }),
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

vi.mock("@/lib/auth", () => ({
  useAuthStore: (selector: (state: { user: typeof mocks.user }) => unknown) =>
    selector({ user: mocks.user }),
}));

function detail(status = "exported") {
  return {
    id: "code-batch-1",
    batch_code: "CB-20260811-001",
    quantity: 100,
    expected_item_count: 100,
    product_id: "product-1",
    product_name: "安心大米",
    sku_id: "sku-1",
    sku_name: "5kg 礼盒",
    production_batch_id: "production-batch-1",
    production_batch_code: "PB-20260811-001",
    code_type: "single",
    generation_mode: "item_level",
    source: "generated",
    status,
    created_by: "account-1",
    stats: { created: 100 },
  };
}

describe("CodeBatchDetailPage boundaries", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.user.role = "admin";
    mocks.user.tenant_type = "brand";
    mocks.user.acting_tenant_id = null;
    mocks.user.agency_scope = null;
    mocks.get.mockResolvedValue({ data: detail() });
  });

  it("fails closed before requesting data for a base agency", async () => {
    mocks.user.tenant_type = "agency";

    render(<CodeBatchDetailPage />);

    expect(await screen.findByRole("alert")).toBeVisible();
    expect(mocks.get).not.toHaveBeenCalled();
  });

  it("exposes only the next delivery transition for an exported batch", async () => {
    render(<CodeBatchDetailPage />);

    expect(
      await screen.findByRole("heading", { name: "CB-20260811-001" })
    ).toBeVisible();
    expect(screen.getByRole("button", { name: /导出码表/ })).toBeEnabled();
    expect(screen.getByRole("button", { name: /标记印刷中/ })).toBeEnabled();
    expect(
      screen.queryByRole("button", { name: /标记已交付/ })
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /激活码批次/ })
    ).not.toBeInTheDocument();
  });

  it("shows a retryable server failure instead of a false not-found result", async () => {
    mocks.get.mockRejectedValue({ response: { status: 500 } });

    render(<CodeBatchDetailPage />);

    expect(await screen.findByRole("alert")).toBeVisible();
    expect(screen.queryByText(/不存在/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /重.*试/ }));
    expect(mocks.get).toHaveBeenCalledTimes(2);
  });
});
