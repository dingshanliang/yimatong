import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import CodeBatchDetailPage from "./page";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  push: vi.fn(),
  modalConfirm: vi.fn(),
  itemStatus: "activated",
  batchStatus: "exported",
  planReadOnly: false,
  riskModuleEnabled: true,
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
        modal: { confirm: (...args: unknown[]) => mocks.modalConfirm(...args) },
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

vi.mock("../../_components/TenantPlanReadOnly", () => ({
  useTenantPlanReadOnly: () => mocks.planReadOnly,
  useTenantFeatureEnabled: () => mocks.riskModuleEnabled,
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
    mocks.itemStatus = "activated";
    mocks.batchStatus = "exported";
    mocks.planReadOnly = false;
    mocks.riskModuleEnabled = true;
    mocks.get.mockImplementation(async (path: string) => {
      if (path === "/code-items") {
        return {
          data: {
            items: [
              {
                id: "code-item-1",
                public_id: "CODE-001",
                status: mocks.itemStatus,
                code_type: "single",
              },
            ],
            total: 1,
          },
        };
      }
      return { data: detail(mocks.batchStatus) };
    });
  });

  it("fails closed before requesting data for a base agency", async () => {
    mocks.user.tenant_type = "agency";

    render(<CodeBatchDetailPage />);

    expect(await screen.findByRole("alert")).toBeVisible();
    expect(mocks.get).not.toHaveBeenCalled();
  });

  it("lets an operator read the batch without mounting raw-code lifecycle requests", async () => {
    mocks.user.role = "operator";

    render(<CodeBatchDetailPage />);

    expect(
      await screen.findByRole("heading", { name: "CB-20260811-001" })
    ).toBeVisible();
    expect(mocks.get).toHaveBeenCalledWith("/code-batches/code-batch-1");
    expect(mocks.get).not.toHaveBeenCalledWith(
      "/code-items",
      expect.anything()
    );
    expect(screen.queryByText("CODE-001")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /^冻\s*结$/ })
    ).not.toBeInTheDocument();
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

  it("requires a reason and confirmation before freezing an active individual code", async () => {
    mocks.post.mockResolvedValue({
      data: { id: "code-item-1", public_id: "CODE-001", status: "frozen" },
    });

    render(<CodeBatchDetailPage />);

    expect(await screen.findByText("CODE-001")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: /冻.*结/ }));
    fireEvent.change(screen.getByLabelText("冻结原因"), {
      target: { value: "异常扫码，人工复核" },
    });
    fireEvent.click(screen.getByRole("button", { name: /确认冻结/ }));
    await waitFor(() =>
      expect(mocks.post).toHaveBeenCalledWith(
        "/risk-alerts/code-items/code-item-1/freeze",
        { reason: "异常扫码，人工复核", confirm: "freeze" }
      )
    );
  });

  it("recovers a frozen individual code to its authoritative prior state", async () => {
    mocks.itemStatus = "frozen";
    mocks.post.mockResolvedValue({
      data: { id: "code-item-1", public_id: "CODE-001", status: "bound" },
    });

    render(<CodeBatchDetailPage />);

    expect(await screen.findByText("CODE-001")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: /恢.*复/ }));
    const confirmation = mocks.modalConfirm.mock.calls[0]?.[0] as {
      onOk: () => Promise<void>;
    };
    await confirmation.onOk();
    expect(mocks.post).toHaveBeenCalledWith(
      "/risk-alerts/code-items/code-item-1/unfreeze"
    );
  });

  it("hides risk freeze and recover controls when the tenant has not enabled risk management", async () => {
    mocks.riskModuleEnabled = false;

    const activeView = render(<CodeBatchDetailPage />);

    expect(await screen.findByText("CODE-001")).toBeVisible();
    expect(
      screen.queryByRole("button", { name: /^冻\s*结$/ })
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "永久作废" })).toBeVisible();
    expect(mocks.post).not.toHaveBeenCalled();

    activeView.unmount();
    mocks.itemStatus = "frozen";
    render(<CodeBatchDetailPage />);
    expect(await screen.findByText("CODE-001")).toBeVisible();
    expect(
      screen.queryByRole("button", { name: /^恢\s*复$/ })
    ).not.toBeInTheDocument();
    expect(mocks.post).not.toHaveBeenCalled();
  });

  it("requires a reason and explicit confirmation before permanently voiding one code", async () => {
    mocks.post.mockResolvedValue({
      data: { id: "code-item-1", status: "revoked" },
    });

    render(<CodeBatchDetailPage />);

    expect(await screen.findByText("CODE-001")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: /永久作废/ }));
    fireEvent.change(screen.getByLabelText("作废原因"), {
      target: { value: "包装破损，停止流通" },
    });
    fireEvent.click(screen.getByRole("button", { name: /确认作废/ }));

    await waitFor(() =>
      expect(mocks.post).toHaveBeenCalledWith(
        "/code-items/code-item-1/revoke",
        { reason: "包装破损，停止流通", confirm: "void" }
      )
    );
  });

  it("requires a reason and confirmation before freezing an activated batch", async () => {
    mocks.batchStatus = "activated";
    mocks.post.mockResolvedValue({ data: { frozen: 1 } });

    render(<CodeBatchDetailPage />);

    expect(await screen.findByText("CODE-001")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "冻结整批" }));
    fireEvent.change(screen.getByLabelText("整批冻结原因"), {
      target: { value: "批次流向异常，暂停核查" },
    });
    fireEvent.click(screen.getByRole("button", { name: /确认冻结整批/ }));

    await waitFor(() =>
      expect(mocks.post).toHaveBeenCalledWith(
        "/code-batches/code-batch-1/freeze",
        { reason: "批次流向异常，暂停核查", confirm: "freeze" }
      )
    );
  });

  it("sends a bounded reason and explicit query confirmation when voiding a batch", async () => {
    mocks.batchStatus = "activated";
    mocks.post.mockResolvedValue({ data: { voided: 1 } });

    render(<CodeBatchDetailPage />);

    expect(await screen.findByText("CODE-001")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "永久作废整批" }));
    fireEvent.change(screen.getByLabelText("整批作废原因"), {
      target: { value: "批次确认报废" },
    });
    fireEvent.click(screen.getByRole("button", { name: /确认作废整批/ }));

    await waitFor(() =>
      expect(mocks.post).toHaveBeenCalledWith(
        "/code-batches/code-batch-1/void",
        null,
        { params: { reason: "批次确认报废", confirm: "void" } }
      )
    );
  });

  it("keeps lifecycle mutations disabled when the tenant plan is read-only", async () => {
    mocks.batchStatus = "activated";

    mocks.planReadOnly = true;
    render(<CodeBatchDetailPage />);

    expect(await screen.findByText("CODE-001")).toBeVisible();
    const freezeItemButton = screen.getByRole("button", {
      name: /^冻\s*结$/,
    });
    expect(freezeItemButton).toBeDisabled();
    expect(screen.getByRole("button", { name: "冻结整批" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "永久作废整批" })).toBeDisabled();

    fireEvent.click(freezeItemButton);
    fireEvent.click(screen.getByRole("button", { name: "冻结整批" }));
    fireEvent.click(screen.getByRole("button", { name: "永久作废整批" }));
    expect(mocks.post).not.toHaveBeenCalled();
  });
});
