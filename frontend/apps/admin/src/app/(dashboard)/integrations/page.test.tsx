import { App } from "antd";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import IntegrationsPage from "./page";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  user: {
    role: "admin",
    tenant_type: "brand",
    acting_tenant_id: null as string | null,
  },
}));

vi.mock("@/lib/api", () => ({
  default: {
    get: mocks.get,
    post: vi.fn(),
    delete: vi.fn(),
  },
  extractErrorMessage: (_error: unknown, fallback: string) => fallback,
}));

vi.mock("@/lib/auth", () => ({
  useAuthStore: (selector: (state: { user: typeof mocks.user }) => unknown) =>
    selector({ user: mocks.user }),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock("./_components/ApiKeysTab", () => ({
  ApiKeysTab: () => <div data-testid="api-key-management" />,
}));

function renderPage() {
  return render(
    <App>
      <IntegrationsPage />
    </App>
  );
}

describe("IntegrationsPage API key entry", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.user.role = "admin";
    mocks.user.tenant_type = "brand";
    mocks.user.acting_tenant_id = null;
    mocks.get.mockResolvedValue({
      data: {
        connected: false,
        status: "unconfigured",
        config: {},
        secrets: {},
      },
    });
  });

  it("shows the API key entry to a brand administrator", () => {
    renderPage();

    expect(screen.getByRole("tab", { name: "API 密钥" })).toBeInTheDocument();
  });

  it.each([
    ["operator", "brand", null],
    ["viewer", "brand", null],
    ["admin", "agency", null],
    ["admin", "agency", "client-tenant"],
  ])(
    "hides the API key entry for %s in %s context",
    (role, tenantType, actingTenantId) => {
      mocks.user.role = role;
      mocks.user.tenant_type = tenantType;
      mocks.user.acting_tenant_id = actingTenantId;

      renderPage();

      expect(
        screen.queryByRole("tab", { name: "API 密钥" })
      ).not.toBeInTheDocument();
    }
  );
});
