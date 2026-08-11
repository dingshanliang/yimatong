import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import TakeoverPage from "./page";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  patch: vi.fn(),
  planReadOnly: false,
  routeStatus: "candidate",
  projectStatus: "ready_for_confirmation",
  user: {
    role: "viewer",
    tenant_type: "brand",
    acting_tenant_id: null as string | null,
    agency_scope: null as string[] | null,
  },
}));

vi.mock("antd", async () => {
  const actual = await vi.importActual<typeof import("antd")>("antd");
  return {
    ...actual,
    App: {
      ...actual.App,
      useApp: () => ({
        message: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
      }),
    },
  };
});

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mocks.get(...args),
    post: (...args: unknown[]) => mocks.post(...args),
    patch: (...args: unknown[]) => mocks.patch(...args),
  },
  extractErrorMessage: (_error: unknown, fallback: string) => fallback,
}));

vi.mock("@/lib/auth", () => ({
  useAuthStore: (selector: (state: { user: typeof mocks.user }) => unknown) =>
    selector({ user: mocks.user }),
}));

vi.mock("../../_components/TenantPlanReadOnly", () => ({
  useTenantPlanReadOnly: () => mocks.planReadOnly,
}));

describe("TakeoverPage authorization", () => {
  const project = {
    id: "project-1",
    name: "旧码接管项目",
    source_system: "legacy",
    mode: "legacy_redirect",
    source_domain: "legacy.example.com",
    domain_verification_record_name: null as string | null,
    domain_verification_record_value: undefined as string | undefined,
    get status() {
      return mocks.projectStatus;
    },
    assessment: {
      code_type: "unique",
      recommended_mode: "legacy_redirect",
      recommendation_reason: "ready",
      capabilities: {},
    },
  };

  function mockReadableProject() {
    mocks.get.mockImplementation(async (path: string) => {
      if (path === "/takeovers")
        return { data: { items: [project], total: 1, page: 1, page_size: 20 } };
      if (path.endsWith("/readiness")) {
        return {
          data: { checks: [], passed_count: 1, total_count: 1, ready: true },
        };
      }
      if (path.endsWith("/imports"))
        return { data: { items: [], total: 0, page: 1, page_size: 20 } };
      if (path.endsWith("/events")) {
        return {
          data: {
            events: [],
            observations: [],
            events_total: 0,
            observations_total: 0,
            page: 1,
            page_size: 20,
          },
        };
      }
      if (path.endsWith("/routes")) {
        return {
          data: {
            total: 1,
            page: 1,
            page_size: 20,
            items: [
              {
                id: "route-1",
                version: 1,
                status: mocks.routeStatus,
                content_digest: "abcdef1234567890",
                sample_codes: ["OLD-1"],
                current_event_id: "event-current",
              },
            ],
          },
        };
      }
      throw new Error(`unexpected GET ${path}`);
    });
  }

  beforeEach(() => {
    vi.clearAllMocks();
    mocks.planReadOnly = false;
    mocks.routeStatus = "candidate";
    mocks.projectStatus = "ready_for_confirmation";
    mocks.user.role = "viewer";
    mocks.user.tenant_type = "brand";
    mocks.user.acting_tenant_id = null;
    mocks.user.agency_scope = null;
    project.mode = "legacy_redirect";
    project.domain_verification_record_name = null;
    project.domain_verification_record_value = undefined;
  });

  it("makes zero requests for viewers", () => {
    render(<TakeoverPage />);

    expect(screen.getByText("当前账户不能访问既有码接管")).toBeVisible();
    expect(mocks.get).not.toHaveBeenCalled();
    expect(mocks.post).not.toHaveBeenCalled();
  });

  it("makes zero requests for a base agency", () => {
    mocks.user.role = "admin";
    mocks.user.tenant_type = "agency";

    render(<TakeoverPage />);

    expect(screen.getByText("当前账户不能访问既有码接管")).toBeVisible();
    expect(mocks.get).not.toHaveBeenCalled();
    expect(mocks.post).not.toHaveBeenCalled();
  });

  it("shows operators only prepare actions", async () => {
    mocks.user.role = "operator";
    mockReadableProject();

    render(<TakeoverPage />);

    expect((await screen.findAllByText("旧码接管项目"))[0]).toBeVisible();
    expect(screen.getByRole("button", { name: /新建接管项目/ })).toBeEnabled();
    expect(screen.getByRole("button", { name: /创建候选版本/ })).toBeEnabled();
    expect(
      screen.queryByRole("button", { name: "品牌确认" })
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "确认版本" })
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "执行切换" })
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "回退" })
    ).not.toBeInTheDocument();
    expect(mocks.post).not.toHaveBeenCalled();
  });

  it("shows the tenant-bound TXT challenge before CNAME verification", async () => {
    mocks.user.role = "operator";
    project.mode = "cname";
    project.domain_verification_record_name =
      "_yimatong.scan.brand.example.com";
    project.domain_verification_record_value =
      "yimatong-verification=project-token";
    mockReadableProject();

    render(<TakeoverPage />);

    expect(await screen.findByText("域名所有权验证")).toBeVisible();
    expect(screen.getByText("_yimatong.scan.brand.example.com")).toBeVisible();
    expect(
      screen.getByText("yimatong-verification=project-token")
    ).toBeVisible();
  });

  it("keeps reads available but disables every mutation for an expired plan", async () => {
    mocks.user.role = "admin";
    mocks.planReadOnly = true;
    mockReadableProject();

    render(<TakeoverPage />);

    expect((await screen.findAllByText("旧码接管项目"))[0]).toBeVisible();
    expect(screen.getByRole("button", { name: /新建接管项目/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /品牌确认/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /创建候选版本/ })).toBeDisabled();
    expect(
      await screen.findByRole("button", { name: "确认版本" })
    ).toBeDisabled();
    expect(mocks.post).not.toHaveBeenCalled();
  }, 60_000);

  it("loads audit evidence and exposes complete only to an admin", async () => {
    mocks.user.role = "admin";
    mocks.routeStatus = "active";
    mockReadableProject();
    mocks.post.mockResolvedValue({ data: {} });

    render(<TakeoverPage />);

    fireEvent.click(await screen.findByRole("button", { name: "完成接管" }));
    await waitFor(() => {
      expect(mocks.get).toHaveBeenCalledWith(
        "/takeovers/project-1/events",
        expect.objectContaining({ params: { page: 1, page_size: 20 } })
      );
      expect(mocks.post).toHaveBeenCalledWith(
        "/takeovers/project-1/routes/route-1/complete",
        null,
        expect.objectContaining({
          params: expect.objectContaining({
            idempotency_key: expect.any(String),
          }),
        })
      );
    });
  }, 60_000);

  it("requests later project and detail pages instead of hiding records after the first page", async () => {
    mocks.user.role = "admin";
    mocks.get.mockImplementation(
      async (
        path: string,
        config?: { params?: { page?: number; page_size?: number } }
      ) => {
        const page = config?.params?.page ?? 1;
        if (path === "/takeovers") {
          return {
            data: {
              items: [
                {
                  ...project,
                  id: `project-${page}`,
                  name: `接管项目第${page}页`,
                },
              ],
              total: 21,
              page,
              page_size: 20,
            },
          };
        }
        if (path.endsWith("/readiness")) {
          return {
            data: { checks: [], passed_count: 1, total_count: 1, ready: true },
          };
        }
        if (path.endsWith("/imports")) {
          return { data: { items: [], total: 21, page, page_size: 20 } };
        }
        if (path.endsWith("/routes")) {
          return { data: { items: [], total: 21, page, page_size: 20 } };
        }
        if (path.endsWith("/events")) {
          return {
            data: {
              events: [],
              observations: [],
              events_total: 21,
              observations_total: 21,
              page,
              page_size: 20,
            },
          };
        }
        throw new Error(`unexpected GET ${path}`);
      }
    );

    render(<TakeoverPage />);

    const projectsCard = (await screen.findByText("项目列表")).closest(
      ".ant-card"
    );
    fireEvent.click(
      projectsCard!.querySelector<HTMLButtonElement>(
        '[title="Next Page"] button'
      )!
    );
    expect((await screen.findAllByText("接管项目第2页"))[0]).toBeVisible();

    for (const title of ["旧码导入", "路由版本", "切换事件与服务端探测"]) {
      const card = screen.getByText(title).closest(".ant-card");
      const next = card!.querySelector<HTMLButtonElement>(
        '[title="Next Page"] button'
      );
      expect(next).not.toBeNull();
      fireEvent.click(next!);
    }

    await waitFor(() => {
      expect(mocks.get).toHaveBeenCalledWith(
        "/takeovers/project-2/imports",
        expect.objectContaining({ params: { page: 2, page_size: 20 } })
      );
      expect(mocks.get).toHaveBeenCalledWith(
        "/takeovers/project-2/routes",
        expect.objectContaining({ params: { page: 2, page_size: 20 } })
      );
      expect(mocks.get).toHaveBeenCalledWith(
        "/takeovers/project-2/events",
        expect.objectContaining({ params: { page: 2, page_size: 20 } })
      );
    });
  }, 60_000);

  it("offers server verification for a durable rolling-back route", async () => {
    mocks.user.role = "admin";
    mocks.routeStatus = "rolling_back";
    mockReadableProject();
    mocks.post.mockResolvedValue({
      data: {
        route: { id: "route-1", status: "rolled_back" },
        observation: { status: "passed", target_match: true },
      },
    });

    render(<TakeoverPage />);

    fireEvent.click(await screen.findByRole("button", { name: "验证回退" }));
    await waitFor(() => {
      expect(mocks.post).toHaveBeenCalledWith(
        "/takeovers/project-1/routes/route-1/rollback/verify",
        null,
        expect.objectContaining({
          params: expect.objectContaining({
            idempotency_key: expect.any(String),
          }),
        })
      );
    });
  }, 60_000);

  it("keeps legacy cutover disabled until current pre-cutover proof exists", async () => {
    mocks.user.role = "admin";
    mocks.routeStatus = "confirmed";
    mocks.projectStatus = "pending_external";
    mockReadableProject();

    render(<TakeoverPage />);

    expect(
      await screen.findByRole("button", { name: "执行切换" })
    ).toBeDisabled();
    expect(mocks.post).not.toHaveBeenCalled();
  }, 60_000);
});
