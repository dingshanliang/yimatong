import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import StorePortalPage from "../page";

const mockGet = vi.fn();

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
  },
}));

describe("StorePortalPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGet.mockResolvedValue({
      data: {
        scope: { type: "store", id: "s1", name: "南京东路店", code: "STORE-NJDL" },
        address: "上海市黄浦区",
        status: "active",
        region_name: "上海区域",
        distributor_name: "华东经销商",
        allocated_quantity: 300,
        allocation_count: 1,
        recent_allocations: [
          {
            id: "a1",
            batch_code: "CB-001",
            product_name: "有机大米",
            quantity: 300,
            remaining_quantity: 700,
          },
        ],
      },
    });
  });

  it("renders store portal summary and allocated batches", async () => {
    render(<StorePortalPage />);

    expect(screen.getByText("门店工作台")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("南京东路店")).toBeInTheDocument());
    expect(screen.getByText("本店收货批次")).toBeInTheDocument();
    expect(screen.getByText("收货数量")).toBeInTheDocument();
    expect(screen.getByText("上海区域")).toBeInTheDocument();
    expect(screen.getByText("华东经销商")).toBeInTheDocument();
    expect(screen.getByText("CB-001")).toBeInTheDocument();
  });
});
