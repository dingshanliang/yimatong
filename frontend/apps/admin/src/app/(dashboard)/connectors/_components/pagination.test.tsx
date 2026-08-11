import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CouponPoolsTab } from "./CouponPoolsTab";
import { DeliveriesTab } from "./DeliveriesTab";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  setPoolPage: vi.fn(),
  message: { error: vi.fn(), success: vi.fn() },
}));

vi.mock("antd", async () => {
  const actual = await vi.importActual<typeof import("antd")>("antd");
  return {
    ...actual,
    App: { ...actual.App, useApp: () => ({ message: mocks.message }) },
  };
});

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mocks.get(...args),
    post: (...args: unknown[]) => mocks.post(...args),
  },
  extractErrorMessage: () => "failed",
}));

vi.mock("@/lib/hooks", () => ({
  useCrud: () => ({
    items: [
      { id: "pool-1", name: "券码池一", total_codes: 100, remaining: 80 },
    ],
    total: 45,
    page: 1,
    pageSize: 20,
    setPage: mocks.setPoolPage,
    loading: false,
    mutate: vi.fn(),
  }),
  usePaginatedList: () => ({
    items: [],
    total: 0,
    page: 1,
    setPage: vi.fn(),
    loading: false,
  }),
}));

describe("connector list pagination", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.get.mockImplementation(
      (_url: string, options?: { params?: { page?: number } }) =>
        Promise.resolve({
          data: {
            items: [
              {
                id: `delivery-${options?.params?.page || 1}`,
                connector_id: "connector-1",
                consumer_id: "consumer-1",
                benefit_type: "coupon",
                status: "pending",
                retry_count: 0,
                max_retries: 5,
                external_data: null,
                next_retry_at: null,
              },
            ],
            total: 21,
          },
        })
    );
  });

  it("routes coupon pool page changes back to the server-backed hook", () => {
    render(<CouponPoolsTab />);

    fireEvent.click(screen.getByTitle("2"));
    expect(mocks.setPoolPage).toHaveBeenCalledWith(2, 20);
  });

  it("requests the selected delivery page from the server", async () => {
    render(<DeliveriesTab />);
    await waitFor(() =>
      expect(mocks.get).toHaveBeenCalledWith(
        "/connectors/deliveries/pending-retries",
        { params: { page: 1, page_size: 20 } }
      )
    );

    fireEvent.click(screen.getByTitle("2"));

    await waitFor(() =>
      expect(mocks.get).toHaveBeenCalledWith(
        "/connectors/deliveries/pending-retries",
        { params: { page: 2, page_size: 20 } }
      )
    );
  });
});
