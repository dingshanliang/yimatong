import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ImportsPage from "./page";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  role: "admin",
  tenantType: "brand",
  actingTenantId: null as string | null,
  agencyScope: null as string[] | null,
  planReadOnly: false,
  message: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

vi.mock("antd", async () => {
  const actual = await vi.importActual<typeof import("antd")>("antd");
  return {
    ...actual,
    App: {
      ...actual.App,
      useApp: () => ({ message: mocks.message }),
    },
  };
});

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mocks.get(...args),
  },
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

vi.mock("../_components/TenantPlanReadOnly", () => ({
  useTenantPlanReadOnly: () => mocks.planReadOnly,
}));

describe("ImportsPage authorization and upload boundary", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.role = "admin";
    mocks.tenantType = "brand";
    mocks.actingTenantId = null;
    mocks.agencyScope = null;
    mocks.planReadOnly = false;
    mocks.get.mockResolvedValue({ data: [] });
    localStorage.setItem("access_token", "access-token");
  });

  it.each([
    {
      role: "viewer",
      tenantType: "brand",
      actingTenantId: null,
      agencyScope: null,
    },
    {
      role: "admin",
      tenantType: "agency",
      actingTenantId: null,
      agencyScope: null,
    },
    {
      role: "operator",
      tenantType: "agency",
      actingTenantId: "client-1",
      agencyScope: ["codes"],
    },
  ])(
    "does not mount or request import data for a denied principal",
    (principal) => {
      mocks.role = principal.role;
      mocks.tenantType = principal.tenantType;
      mocks.actingTenantId = principal.actingTenantId;
      mocks.agencyScope = principal.agencyScope;

      const { container } = render(<ImportsPage />);

      expect(screen.getByRole("alert")).toBeVisible();
      expect(container.querySelector('input[type="file"]')).toBeNull();
      expect(mocks.get).not.toHaveBeenCalled();
    }
  );

  it.each([
    {
      role: "admin",
      tenantType: "brand",
      actingTenantId: null,
      agencyScope: null,
    },
    {
      role: "operator",
      tenantType: "brand",
      actingTenantId: null,
      agencyScope: null,
    },
    {
      role: "operator",
      tenantType: "agency",
      actingTenantId: "client-1",
      agencyScope: ["products"],
    },
  ])(
    "mounts the Excel workflow for an authorized principal",
    async (principal) => {
      mocks.role = principal.role;
      mocks.tenantType = principal.tenantType;
      mocks.actingTenantId = principal.actingTenantId;
      mocks.agencyScope = principal.agencyScope;

      const { container } = render(<ImportsPage />);

      const input =
        container.querySelector<HTMLInputElement>('input[type="file"]');
      expect(input).not.toBeNull();
      expect(input).toHaveAttribute("accept", ".xlsx");
      await waitFor(() => {
        expect(mocks.get).toHaveBeenCalledWith("/imports/records");
      });
    }
  );

  it("rejects a dragged legacy .xls file before any upload request", async () => {
    const open = vi.spyOn(XMLHttpRequest.prototype, "open");
    const { container } = render(<ImportsPage />);
    const input =
      container.querySelector<HTMLInputElement>('input[type="file"]');
    expect(input).not.toBeNull();

    fireEvent.change(input!, {
      target: {
        files: [
          new File(["legacy"], "legacy.xls", {
            type: "application/vnd.ms-excel",
          }),
        ],
      },
    });

    await waitFor(() => expect(mocks.message.error).toHaveBeenCalledOnce());
    expect(open).not.toHaveBeenCalled();
  });

  it("keeps reads available but prevents upload when the tenant plan is read-only", async () => {
    mocks.planReadOnly = true;
    const open = vi.spyOn(XMLHttpRequest.prototype, "open");
    const { container } = render(<ImportsPage />);
    const input =
      container.querySelector<HTMLInputElement>('input[type="file"]');

    expect(input).toBeDisabled();
    await waitFor(() => {
      expect(mocks.get).toHaveBeenCalledWith("/imports/records");
    });
    fireEvent.change(input!, {
      target: {
        files: [
          new File(["xlsx"], "catalog.xlsx", {
            type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
          }),
        ],
      },
    });
    expect(open).not.toHaveBeenCalled();
  });
});
