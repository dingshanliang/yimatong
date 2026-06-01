import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import BenefitsPage from "../page";

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
}));

const mockSetFilter = vi.fn();
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
            status: "claimed",
            delivery_status: "not_required",
            claimed_at: "2026-06-01T10:00:00Z",
          },
        ],
        total: 1,
        page: 1,
        pageSize: 20,
        loading: false,
        setPage: vi.fn(),
        setFilter: mockSetFilter,
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
          config_json: { amount: 10, min_order: 50, validity_type: "campaign_period" },
        },
      ],
      total: 1,
      page: 1,
      pageSize: 20,
      loading: false,
      filters: {},
      setPage: vi.fn(),
      setFilter: mockSetFilter,
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
      if (url === "/campaigns") return Promise.resolve({ data: { items: [{ id: "c1", name: "活动1" }], total: 1 } });
      if (url === "/connectors/connectors") return Promise.resolve({ data: [] });
      return Promise.resolve({ data: { items: [], total: 0 } });
    });
  });

  it("renders benefit workbench summary and list", async () => {
    render(<BenefitsPage />);
    expect(screen.getByText("权益管理")).toBeInTheDocument();
    expect(screen.getByText("管理可复用权益、活动使用关系、库存与消费者领取记录。")).toBeInTheDocument();
    expect(screen.getByText("测试权益")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("剩余库存")).toBeInTheDocument());
  });

  it("opens create modal with business-oriented fields", () => {
    render(<BenefitsPage />);
    fireEvent.click(screen.getByRole("button", { name: /新建权益/ }));

    expect(screen.getByText("基本信息")).toBeInTheDocument();
    expect(screen.getByText("发放规则")).toBeInTheDocument();
    expect(screen.getByText("权益类型")).toBeInTheDocument();
    expect(screen.getByText("每位消费者限领")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /创建权益/ })).toBeInTheDocument();
  });

  it("displays stock as total, used, and remaining", () => {
    render(<BenefitsPage />);
    expect(screen.getByText("已领 10 / 100，剩余 90")).toBeInTheDocument();
  });

  it("shows enriched claim records", async () => {
    render(<BenefitsPage />);
    fireEvent.click(screen.getByText("领取记录"));
    expect(await screen.findByText("活动1")).toBeInTheDocument();
    expect(screen.getByText("测试权益")).toBeInTheDocument();
    expect(screen.getByText("无需发放")).toBeInTheDocument();
  });

  it("filters benefits by search keyword", () => {
    render(<BenefitsPage />);
    const search = screen.getByPlaceholderText("搜索权益名称");
    fireEvent.change(search, { target: { value: "复购" } });
    fireEvent.keyDown(search, { key: "Enter", code: "Enter" });
    expect(mockSetFilter).toHaveBeenCalled();
  });
});
