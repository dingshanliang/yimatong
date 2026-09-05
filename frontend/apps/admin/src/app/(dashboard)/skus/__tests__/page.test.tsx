import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import SKUsPage from "../page";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  useCrud: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock("antd", async () => {
  const actual = await vi.importActual<typeof import("antd")>("antd");
  return {
    ...actual,
    App: {
      ...actual.App,
      useApp: () => ({ message: { success: vi.fn(), error: vi.fn() } }),
    },
  };
});

vi.mock("@/lib/api", () => ({
  default: { get: (...args: unknown[]) => mocks.get(...args) },
}));

vi.mock("@/lib/hooks", () => ({
  useCrud: (...args: unknown[]) => mocks.useCrud(...args),
}));

vi.mock("@/lib/auth", () => ({
  useAuthStore: (selector: (state: { user: object }) => unknown) =>
    selector({
      user: {
        role: "operator",
        tenant_type: "brand",
        acting_tenant_id: null,
        agency_scope: null,
      },
    }),
}));

vi.mock("@/components/SKUFormFields", () => ({
  default: () => <div data-testid="sku-fields" />,
  buildSkuPayload: vi.fn(),
}));

describe("SKUsPage dependent options", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.useCrud.mockReturnValue({
      items: [],
      total: 0,
      page: 1,
      loading: false,
      error: undefined,
      filters: {},
      setPage: vi.fn(),
      setFilter: vi.fn(),
      create: vi.fn(),
      update: vi.fn(),
      retry: vi.fn(),
    });
  });

  it("shows product option failure and keeps dependent creation disabled", async () => {
    mocks.get.mockRejectedValueOnce(new Error("network unavailable"));

    render(<SKUsPage />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /新建 SKU/ })).toBeDisabled();
    });
    expect(screen.getByRole("button", { name: /重\s*试/ })).toBeEnabled();
    expect(screen.getByRole("alert")).toBeVisible();
  });
});
