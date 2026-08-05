import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import CampaignAnalyticsPage from "../../campaign-analytics/page";
import CampaignAnalyticsContent from "../_components/CampaignAnalyticsContent";

const mockMessageSuccess = vi.fn();
const mockMessageError = vi.fn();

vi.mock("antd", async () => {
  const actual = await vi.importActual<typeof import("antd")>("antd");
  return {
    ...actual,
    App: {
      useApp: () => ({
        message: { success: mockMessageSuccess, error: mockMessageError },
      }),
    },
  };
});

const mockGet = vi.fn();
const mockPost = vi.fn();

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
  },
  extractErrorMessage: (_err: unknown, fallback = "操作失败") => fallback,
}));

vi.mock("@ant-design/charts", () => ({
  Line: () => null,
}));

/**
 * Walk up the React fiber tree from a DOM element to find the Select onChange handler.
 */
function findSelectOnChange(
  element: Element
): ((value: string) => void) | null {
  const fiberKey = Object.keys(element).find((k) =>
    k.startsWith("__reactFiber$")
  );
  if (!fiberKey) return null;

  let fiber = (element as Record<string, unknown>)[fiberKey] as Record<
    string,
    unknown
  > | null;
  let depth = 0;
  while (fiber && depth < 15) {
    const props = fiber.memoizedProps as Record<string, unknown> | undefined;
    if (props && typeof props.onChange === "function") {
      return props.onChange as (value: string) => void;
    }
    fiber = fiber.return as Record<string, unknown> | null;
    depth++;
  }
  return null;
}

describe("CampaignAnalyticsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGet.mockImplementation((url: string) => {
      if (url === "/analytics/scan-stats") {
        return Promise.resolve({
          data: [
            {
              date: "2026-05-26",
              total_scans: 10,
              uv: 8,
              first_scans: 6,
              rescans: 4,
            },
          ],
        });
      }
      if (url === "/code-batches") {
        return Promise.resolve({
          data: {
            items: [
              {
                id: "b1",
                batch_code: "B001",
                quantity: 100,
                product_name: "有机大米",
              },
            ],
            total: 1,
          },
        });
      }
      if (url === "/campaigns") {
        return Promise.resolve({
          data: { items: [{ id: "c1", name: "春季促销" }], total: 1 },
        });
      }
      if (url?.includes("/analytics/code-stats")) {
        return Promise.resolve({
          data: { total: 100, by_status: { activated: 60, bound: 40 } },
        });
      }
      return Promise.resolve({ data: [] });
    });
  });

  it("renders page title and campaign selector", async () => {
    render(<CampaignAnalyticsPage />);
    await waitFor(() =>
      expect(screen.getByText("活动看板")).toBeInTheDocument()
    );
    expect(screen.getByText("总扫码")).toBeInTheDocument();
  });

  it("uses only analytics endpoints in analytics-restricted agency context", async () => {
    render(<CampaignAnalyticsContent restrictedToAnalytics />);

    await waitFor(() => {
      expect(mockGet).toHaveBeenCalledWith(
        "/analytics/scan-stats",
        expect.any(Object)
      );
    });
    expect(mockGet).not.toHaveBeenCalledWith(
      "/code-batches",
      expect.any(Object)
    );
    expect(mockGet).not.toHaveBeenCalledWith("/campaigns", expect.any(Object));
    expect(screen.queryByText("码批次统计")).not.toBeInTheDocument();
  });

  it("passes codeStatsLoading to batch stats Card loading prop", async () => {
    // Make code-stats hang so codeStatsLoading stays true
    mockGet.mockImplementation((url: string) => {
      if (url === "/analytics/scan-stats") {
        return Promise.resolve({
          data: [
            {
              date: "2026-05-26",
              total_scans: 10,
              uv: 8,
              first_scans: 6,
              rescans: 4,
            },
          ],
        });
      }
      if (url === "/code-batches") {
        return Promise.resolve({
          data: {
            items: [
              {
                id: "b1",
                batch_code: "B001",
                quantity: 100,
                product_name: "有机大米",
              },
            ],
            total: 1,
          },
        });
      }
      if (url === "/campaigns") {
        return Promise.resolve({
          data: { items: [{ id: "c1", name: "春季促销" }], total: 1 },
        });
      }
      if (url?.includes("/analytics/code-stats")) {
        // Never resolves — codeStatsLoading should stay true
        return new Promise(() => {});
      }
      return Promise.resolve({ data: [] });
    });

    const { container } = render(<CampaignAnalyticsPage />);
    await waitFor(() =>
      expect(screen.getByText("码批次统计")).toBeInTheDocument()
    );

    // Get all Select elements on the page; second one is the batch selector
    const selects = container.querySelectorAll(".ant-select");
    expect(selects.length).toBeGreaterThanOrEqual(2);
    const batchSelectEl = selects[1];

    // Extract onChange from the React fiber tree
    const onChange = findSelectOnChange(batchSelectEl);
    expect(onChange).toBeTruthy();

    // Trigger batch selection — this calls setSelectedBatch("b1")
    // which triggers fetchCodeStats, which hangs, so codeStatsLoading stays true
    await act(async () => {
      onChange!("b1");
    });

    // The "码批次统计" Card should now show loading skeleton
    // because loading={codeStatsLoading} renders ant-card-loading + ant-skeleton
    const batchCard = screen.getByText("码批次统计").closest(".ant-card")!;
    await waitFor(() => {
      expect(batchCard.classList.contains("ant-card-loading")).toBe(true);
    });
  }, 10000);
});
