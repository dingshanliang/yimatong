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
    mockGet.mockImplementation((url: string) => {
      if (url === "/channels/portal/distributor/summary") {
        return Promise.resolve({
          data: {
            scope: { type: "distributor", id: "d1", name: "华东经销商" },
            region_count: 2,
            store_count: 4,
            allocated_quantity: 800,
            pending_diversion_count: 3,
            allocation_count: 2,
            regions: [
              {
                id: "r1",
                name: "上海区域",
                city: "上海",
                store_count: 2,
                allocated_quantity: 300,
              },
            ],
            recent_allocations: [
              {
                id: "a1",
                batch_code: "CB-001",
                product_name: "有机大米",
                region_name: "上海区域",
                store_name: "南京东路店",
                quantity: 300,
                remaining_quantity: 700,
              },
            ],
          },
        });
      }
      if (url === "/channels/portal/distributor/diversion-alerts") {
        return Promise.resolve({
          data: {
            items: [
              {
                id: "n1",
                title: "疑似窜货：QR001",
                detail: "预期区域：上海，实际扫码城市：北京",
                read: false,
              },
            ],
            total: 1,
            page: 1,
            page_size: 10,
          },
        });
      }
      return Promise.resolve({ data: { items: [] } });
    });
  });

  it("renders distributor portal summary and allocations", async () => {
    render(<ChannelPortalPage />);

    expect(screen.getByText("经销商工作台")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByText("华东经销商")).toBeInTheDocument()
    );
    expect(screen.getByText("待处理异常")).toBeInTheDocument();
    expect(screen.getByText("区域覆盖")).toBeInTheDocument();
    expect(screen.getByText("最近收货流向")).toBeInTheDocument();
    expect(screen.getByText("收货数量")).toBeInTheDocument();
    expect(screen.getByText("上海区域")).toBeInTheDocument();
    expect(screen.getByText("CB-001")).toBeInTheDocument();
    expect(screen.getByText("南京东路店")).toBeInTheDocument();
  });

  it("loads diversion alerts from the scoped portal endpoint, not risk-notifications", async () => {
    render(<ChannelPortalPage />);

    expect(await screen.findByText("疑似窜货：QR001")).toBeInTheDocument();
    expect(
      screen.getByText("预期区域：上海，实际扫码城市：北京")
    ).toBeInTheDocument();
    expect(screen.getByText("未读")).toBeInTheDocument();
    expect(mockGet).toHaveBeenCalledWith(
      "/channels/portal/distributor/diversion-alerts",
      {
        params: { page_size: 10 },
      }
    );
    expect(mockGet).not.toHaveBeenCalledWith(
      "/risk-notifications",
      expect.anything()
    );
  });
});
