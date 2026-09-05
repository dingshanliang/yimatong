import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  render,
  screen,
  fireEvent,
  waitFor,
  within,
} from "@testing-library/react";
import BenefitsPage from "../page";

const mockAuth = vi.hoisted(() => ({
  role: "admin",
  tenantType: "brand",
  actingTenantId: null as string | null,
  agencyScope: null as string[] | null,
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
        role: mockAuth.role,
        tenant_type: mockAuth.tenantType,
        acting_tenant_id: mockAuth.actingTenantId,
        agency_scope: mockAuth.agencyScope,
      },
    }),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

const mockMessageSuccess = vi.fn();
const mockMessageError = vi.fn();

vi.mock("antd", async () => {
  const actual = await vi.importActual<typeof import("antd")>("antd");
  return {
    ...actual,
    App: {
      useApp: () => ({
        message: {
          success: mockMessageSuccess,
          error: mockMessageError,
        },
      }),
    },
  };
});

const mockGet = vi.fn();
const mockPost = vi.fn();
const mockPatch = vi.fn();
const mockDelete = vi.fn();

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
    patch: (...args: unknown[]) => mockPatch(...args),
    delete: (...args: unknown[]) => mockDelete(...args),
  },
  extractErrorMessage: (err: unknown, fallback = "操作失败") =>
    err instanceof Error && err.message ? err.message : fallback,
}));

const mockSetBenefitFilter = vi.fn();
const mockSetClaimFilter = vi.fn();
const mockMutate = vi.fn();

vi.mock("@/lib/hooks", () => ({
  useCrud: vi.fn((basePath: string) => {
    if (basePath === "/benefits/admin/claims") {
      return {
        items: [
          {
            id: "cl1",
            consumer_id: "consumer-001-abcdef",
            benefit_id: "b1",
            benefit_name: "测试权益",
            campaign_id: "c1",
            campaign_name: "活动1",
            status: "success",
            delivery_status: "failed",
            claimed_at: null,
            latest_delivery_id: "d1",
            latest_delivery_status: "failed",
            delivery_retry_count: 2,
            delivery_next_retry_at: "2026-06-01T12:00:00Z",
          },
        ],
        total: 1,
        page: 1,
        pageSize: 20,
        loading: false,
        setPage: vi.fn(),
        setFilter: mockSetClaimFilter,
        resetFilters: vi.fn(),
        mutate: mockMutate,
        create: vi.fn(),
        update: vi.fn(),
        remove: vi.fn(),
      };
    }
    return {
      items: [
        {
          id: "b1",
          name: "测试权益",
          benefit_type: "platform_coupon",
          stock_total: 100,
          stock_used: 10,
          per_person_limit: 2,
          campaign_id: "c1",
          connector_id: null,
          status: "active",
          created_at: "2026-05-29T10:00:00Z",
          config_json: {
            amount: 10,
            min_order: 50,
            validity_type: "campaign_period",
          },
        },
      ],
      total: 1,
      page: 1,
      pageSize: 20,
      loading: false,
      filters: {},
      setPage: vi.fn(),
      setFilter: mockSetBenefitFilter,
      resetFilters: vi.fn(),
      mutate: mockMutate,
      create: vi.fn(),
      update: vi.fn(),
      remove: vi.fn(),
    };
  }),
}));

describe("BenefitsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockAuth.role = "admin";
    mockAuth.tenantType = "brand";
    mockAuth.actingTenantId = null;
    mockAuth.agencyScope = null;
    mockGet.mockImplementation((url: string) => {
      if (url === "/benefits/summary") {
        return Promise.resolve({
          data: {
            total: 1,
            active: 1,
            unused: 0,
            stock_total: 100,
            stock_used: 10,
            stock_remaining: 90,
            claim_count: 1,
            failed_delivery_count: 0,
          },
        });
      }
      if (url === "/campaigns")
        return Promise.resolve({
          data: { items: [{ id: "c1", name: "活动1" }], total: 1 },
        });
      if (url === "/connectors/connectors")
        return Promise.resolve({ data: [] });
      if (url === "/connectors/deliveries/d1") {
        return Promise.resolve({
          data: {
            id: "d1",
            consumer_id: "consumer-001-abcdef",
            status: "failed",
            retry_count: 2,
            max_retries: 5,
            external_data: { error: "invalid receiver" },
            next_retry_at: "2026-06-01T12:00:00Z",
            created_at: "2026-06-01T11:00:00Z",
            updated_at: "2026-06-01T11:05:00Z",
          },
        });
      }
      return Promise.resolve({ data: { items: [], total: 0 } });
    });
    mockPost.mockResolvedValue({ data: { status: "success" } });
  });

  it("does not mount benefit requests for a viewer", () => {
    mockAuth.role = "viewer";

    render(<BenefitsPage />);

    expect(mockGet).not.toHaveBeenCalled();
    expect(mockPost).not.toHaveBeenCalled();
    expect(mockPatch).not.toHaveBeenCalled();
    expect(mockDelete).not.toHaveBeenCalled();
  });

  it("renders benefit workbench summary and list", async () => {
    render(<BenefitsPage />);
    expect(screen.getByText("权益管理")).toBeInTheDocument();
    expect(
      screen.getByText("管理可复用权益、活动使用关系、库存与消费者领取记录。")
    ).toBeInTheDocument();
    expect(screen.getByText("测试权益")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByText("剩余库存")).toBeInTheDocument()
    );
  });

  it("falls back to visible list data when summary is unavailable", async () => {
    mockGet.mockImplementation((url: string) => {
      if (url === "/benefits/summary")
        return Promise.reject(new Error("summary route unavailable"));
      if (url === "/campaigns")
        return Promise.resolve({
          data: { items: [{ id: "c1", name: "活动1" }], total: 1 },
        });
      if (url === "/connectors/connectors")
        return Promise.resolve({ data: [] });
      return Promise.resolve({ data: { items: [], total: 0 } });
    });

    render(<BenefitsPage />);

    const activeCard = screen.getByText("启用中").closest(".ant-card");
    expect(activeCard).not.toBeNull();
    await waitFor(() =>
      expect(
        within(activeCard as HTMLElement).getByText("1")
      ).toBeInTheDocument()
    );
  });

  it("opens create modal with business-oriented fields", () => {
    render(<BenefitsPage />);
    fireEvent.click(screen.getByRole("button", { name: /新建权益/ }));

    expect(screen.getByText("基本信息")).toBeInTheDocument();
    expect(screen.getByText("发放规则")).toBeInTheDocument();
    expect(screen.getAllByText("权益类型").length).toBeGreaterThan(0);
    expect(screen.getByText("每位消费者限领")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /创建权益/ })
    ).toBeInTheDocument();
  });

  it("displays stock as total, used, and remaining", () => {
    render(<BenefitsPage />);
    expect(screen.getByText("已领 10 / 100，剩余 90")).toBeInTheDocument();
  });

  it("shows enriched claim records", async () => {
    render(<BenefitsPage />);
    fireEvent.click(screen.getByRole("tab", { name: "领取记录" }));
    expect(await screen.findByText("活动1")).toBeInTheDocument();
    expect(screen.getAllByText("测试权益").length).toBeGreaterThan(0);
    expect(screen.getByText("已领取")).toBeInTheDocument();
    expect(screen.queryByText("success")).not.toBeInTheDocument();
    expect(screen.getByText("发放失败")).toBeInTheDocument();
    expect(screen.getByText("时间未知")).toBeInTheDocument();
  });

  it("filters benefits by search keyword", () => {
    render(<BenefitsPage />);
    const search = screen.getByPlaceholderText("搜索权益名称");
    fireEvent.change(search, { target: { value: "复购" } });
    fireEvent.keyDown(search, { key: "Enter", code: "Enter" });
    expect(mockSetBenefitFilter).toHaveBeenCalled();
  });

  it("filters claims with dedicated claim filters", async () => {
    render(<BenefitsPage />);
    fireEvent.click(screen.getByRole("tab", { name: "领取记录" }));

    const search = await screen.findByPlaceholderText("搜索消费者/权益/活动");
    fireEvent.change(search, { target: { value: "consumer-001" } });
    fireEvent.keyDown(search, { key: "Enter", code: "Enter" });

    expect(mockSetClaimFilter).toHaveBeenCalledWith({ q: "consumer-001" });
  });

  it("opens claim delivery detail and defers failed delivery retries to the system", async () => {
    render(<BenefitsPage />);
    fireEvent.click(screen.getByRole("tab", { name: "领取记录" }));

    fireEvent.click(await screen.findByRole("button", { name: /查看详情/ }));

    expect(await screen.findByText("领取详情")).toBeInTheDocument();
    await waitFor(() =>
      expect(document.body.textContent).toContain("invalid receiver")
    );

    // PG 环境的重试端点对未成功发放固定返回 409（系统自动重试），
    // 页面不得再展示人工重试入口。
    expect(screen.getByText("系统将自动重试")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /重试发放/ })
    ).not.toBeInTheDocument();
    expect(mockPost).not.toHaveBeenCalled();
  });
});
