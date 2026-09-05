import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ImportsPage, {
  EXCEL_UPLOAD_ACTION,
  summarizeImportResponse,
} from "./page";

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
  API_BASE_URL: "http://test-api:8000",
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
      // /imports/records 端点不存在：页面加载不得再请求导入记录
      expect(mocks.get).not.toHaveBeenCalled();
      expect(
        screen.getByText(/导入记录暂不支持保存与回查/)
      ).toBeInTheDocument();
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
    expect(mocks.get).not.toHaveBeenCalled();
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

  it("uploads against the shared axios API origin", () => {
    mocks.role = "admin";
    render(<ImportsPage />);
    // action 必须从 @/lib/api 的共享 origin 常量拼接，与 axios baseURL 保持一致
    expect(EXCEL_UPLOAD_ACTION).toBe(
      "http://test-api:8000/api/v1/imports/excel"
    );
  });

  it("summarizes an HTTP 200 success:false parse result with row errors", () => {
    const outcome = summarizeImportResponse({
      success: false,
      message: "文件解析失败",
      created: 0,
      updated: 0,
      errors: [
        { sheet: "产品", row: 3, message: "产品名称不能为空" },
        { sheet: "SKU", row: 5, message: "SKU 编码重复" },
      ],
    });

    expect(outcome.ok).toBe(false);
    if (!outcome.ok) {
      expect(outcome.failure.summary).toBe("解析完成：成功 0 条，失败 2 条");
      expect(outcome.failure.backendMessage).toBe("文件解析失败");
      expect(outcome.failure.rows).toHaveLength(2);
    }
  });

  it("counts partial-execution failures against created rows and truncates the shown rows", () => {
    const errors = Array.from({ length: 9 }, (_, i) => ({
      sheet: "批次",
      row: i + 2,
      message: `批次码重复 ${i + 1}`,
    }));
    const outcome = summarizeImportResponse({
      success: false,
      message: "导入完成，有 9 个错误",
      created: 4,
      updated: 1,
      errors,
    });

    expect(outcome.ok).toBe(false);
    if (!outcome.ok) {
      expect(outcome.failure.summary).toBe("解析完成：成功 5 条，失败 9 条");
      expect(outcome.failure.rows).toHaveLength(5);
      expect(outcome.failure.totalErrors).toBe(9);
    }
  });

  it("treats a success response without the success flag as created/updated counts", () => {
    const outcome = summarizeImportResponse({ created: 3, updated: 2 });

    expect(outcome).toEqual({ ok: true, created: 3, updated: 2 });
  });
});
