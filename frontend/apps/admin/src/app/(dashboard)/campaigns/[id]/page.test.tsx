import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import CampaignDetailPage from "./page";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  role: "admin",
  tenantType: "brand",
  actingTenantId: null as string | null,
  agencyScope: null as string[] | null,
  message: { error: vi.fn() },
}));

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "campaign-1" }),
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock("antd", async () => {
  const actual = await vi.importActual<typeof import("antd")>("antd");
  return {
    ...actual,
    App: { ...actual.App, useApp: () => ({ message: mocks.message }) },
  };
});

vi.mock("@/lib/api", () => ({
  default: { get: (...args: unknown[]) => mocks.get(...args) },
  extractErrorMessage: (_error: unknown, fallback: string) => fallback,
}));

vi.mock("@/lib/auth", () => ({
  useAuthStore: (
    selector: (state: {
      user: {
        role: string;
        tenant_type: string;
        acting_tenant_id: string | null;
        agency_scope: string[] | null;
      };
    }) => unknown
  ) =>
    selector({
      user: {
        role: mocks.role,
        tenant_type: mocks.tenantType,
        acting_tenant_id: mocks.actingTenantId,
        agency_scope: mocks.agencyScope,
      },
    }),
}));

const campaign = {
  id: "campaign-1",
  name: "扫码领券",
  campaign_type: "coupon",
  status: "active",
  start_at: "2026-08-01T00:00:00Z",
  end_at: "2026-09-01T00:00:00Z",
  stock_total: 100,
  stock_used: 1,
  benefit_count: 21,
  claim_count: 1,
  wecom_add_count: 0,
};

describe("Campaign detail authority and pagination", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.role = "admin";
    mocks.tenantType = "brand";
    mocks.actingTenantId = null;
    mocks.agencyScope = null;
    mocks.get.mockImplementation(
      (url: string, config?: { params?: { page?: number } }) => {
        if (url === "/campaigns/campaign-1")
          return Promise.resolve({ data: campaign });
        if (url === "/campaigns/campaign-1/benefits") {
          const page = config?.params?.page || 1;
          return Promise.resolve({
            data: {
              items: [
                {
                  id: `benefit-${page}`,
                  name: `第${page}页权益`,
                  benefit_type: "coupon",
                  stock_total: 10,
                  stock_used: 1,
                  per_person_limit: 1,
                },
              ],
              total: 21,
            },
          });
        }
        return Promise.reject(new Error("unexpected request"));
      }
    );
  });

  it("does not request campaign data for a viewer", () => {
    mocks.role = "viewer";

    render(<CampaignDetailPage />);

    expect(mocks.get).not.toHaveBeenCalled();
  });

  it("loads later benefit pages from the server", async () => {
    render(<CampaignDetailPage />);
    expect(await screen.findByText("第1页权益")).toBeVisible();

    fireEvent.click(screen.getByTitle("2"));

    expect(await screen.findByText("第2页权益")).toBeVisible();
    await waitFor(() =>
      expect(mocks.get).toHaveBeenCalledWith("/campaigns/campaign-1/benefits", {
        params: { page: 2, page_size: 20 },
      })
    );
  });
});
