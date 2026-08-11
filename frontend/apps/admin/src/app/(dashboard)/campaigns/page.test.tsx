import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import CampaignsPage from "./page";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  role: "viewer",
  enabled: undefined as boolean | undefined,
  message: { error: vi.fn(), success: vi.fn(), warning: vi.fn() },
}));

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

vi.mock("antd", async () => {
  const actual = await vi.importActual<typeof import("antd")>("antd");
  return {
    ...actual,
    App: {
      ...actual.App,
      useApp: () => ({ message: mocks.message, modal: { confirm: vi.fn() } }),
    },
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
        acting_tenant_id: null;
        agency_scope: null;
      };
    }) => unknown
  ) =>
    selector({
      user: {
        role: mocks.role,
        tenant_type: "brand",
        acting_tenant_id: null,
        agency_scope: null,
      },
    }),
}));

vi.mock("@/lib/hooks", () => ({
  useCrud: (_path: string, opts: { enabled?: boolean }) => {
    mocks.enabled = opts.enabled;
    return {
      items: [],
      total: 0,
      page: 1,
      loading: false,
      filters: {},
      setPage: vi.fn(),
      setFilter: vi.fn(),
      resetFilters: vi.fn(),
      mutate: vi.fn(),
      update: vi.fn(),
      remove: vi.fn(),
    };
  },
}));

describe("CampaignsPage access fence", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.role = "viewer";
    mocks.enabled = undefined;
  });

  it("keeps viewer requests disabled", () => {
    render(<CampaignsPage />);

    expect(screen.getByText("当前账号无权查看活动")).toBeVisible();
    expect(mocks.enabled).toBe(false);
    expect(mocks.get).not.toHaveBeenCalled();
  });
});
