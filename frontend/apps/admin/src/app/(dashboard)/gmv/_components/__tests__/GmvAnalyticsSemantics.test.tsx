import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

import { DashboardTab } from "../DashboardTab";
import { ROITab } from "../ROITab";

const mockGet = vi.fn();

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
  },
}));

describe("GMV analytics semantics", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGet.mockImplementation((url: string) => {
      if (url.startsWith("/gmv/dashboard")) {
        return Promise.resolve({
          data: {
            total_gmv: 200,
            attributed_orders: 2,
            total_orders: 4,
            unattributed_orders: 2,
            quarantined_order_count: 0,
            order_data_quality: "complete",
            attribution_rate: null,
            attribution_rate_status: "unavailable_non_cohort",
            daily_trend: [],
            by_channel: [],
            by_campaign: [],
          },
        });
      }
      return Promise.resolve({
        data: [
          {
            campaign_id: "019e887f-0000-7000-a000-000000000001",
            campaign_name: "确认活动",
            status: "active",
            budget: 100,
            attributed_gmv: 200,
            attributed_orders: 2,
            scan_count: null,
            scan_uv: null,
            scan_cost: null,
            conversion_rate: null,
            conversion_rate_status:
              "unavailable_missing_campaign_eligible_cohort",
            roi: 2,
            avg_confidence: 1,
          },
        ],
      });
    });
  });

  it("shows occurrence facts and never renders a synthetic dashboard attribution rate", async () => {
    render(<DashboardTab />);

    await waitFor(() =>
      expect(screen.getByText("归因率暂不可用")).toBeInTheDocument()
    );
    expect(screen.getByText("确认归因 GMV（发生口径）")).toBeInTheDocument();
    expect(screen.getByText("确认归因订单（发生口径）")).toBeInTheDocument();
    expect(screen.queryByText("50%")).not.toBeInTheDocument();
  });

  it("omits converted-only scan denominators from campaign ROI", async () => {
    render(<ROITab />);

    await waitFor(() =>
      expect(screen.getByText("确认活动")).toBeInTheDocument()
    );
    expect(screen.getByText("活动转化率暂不可用")).toBeInTheDocument();
    expect(
      screen.getByText("确认归因 GMV 合计（发生口径）")
    ).toBeInTheDocument();
    expect(screen.queryByText("扫码次数")).not.toBeInTheDocument();
    expect(screen.queryByText("扫码 UV")).not.toBeInTheDocument();
    expect(screen.queryByText("扫码成本")).not.toBeInTheDocument();
  });
});
