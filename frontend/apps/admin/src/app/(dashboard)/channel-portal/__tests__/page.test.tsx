import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import ChannelPortalPage from "../page";

const mockGet = vi.fn();

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
  },
}));

describe("ChannelPortalPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGet.mockResolvedValue({
      data: {
        scope: { type: "distributor", id: "d1", name: "华东经销商" },
        region_count: 2,
        store_count: 4,
        allocated_quantity: 800,
        pending_diversion_count: 3,
        allocation_count: 2,
        recent_allocations: [
          {
            id: "a1",
            batch_code: "CB-001",
            product_name: "有机大米",
            store_name: "南京东路店",
            quantity: 300,
            remaining_quantity: 700,
          },
        ],
      },
    });
  });

  it("renders distributor portal summary and allocations", async () => {
    render(<ChannelPortalPage />);

    expect(screen.getByText("经销商工作台")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("华东经销商")).toBeInTheDocument());
    expect(screen.getByText("待处理异常")).toBeInTheDocument();
    expect(screen.getByText("CB-001")).toBeInTheDocument();
    expect(screen.getByText("南京东路店")).toBeInTheDocument();
  });
});
