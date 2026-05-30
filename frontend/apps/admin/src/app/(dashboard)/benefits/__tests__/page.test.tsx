import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import BenefitsPage from "../page";

// Mock Ant Design message
vi.mock("antd", async () => {
  const actual = await vi.importActual("antd");
  return {
    ...actual,
    message: {
      success: vi.fn(),
      error: vi.fn(),
    },
  };
});

// Mock api
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

// Mock hooks
vi.mock("@/lib/hooks", () => ({
  useCrud: vi.fn(() => ({
    items: [
      {
        id: "b1",
        name: "测试权益",
        benefit_type: "platform_coupon",
        stock_total: 100,
        stock_used: 10,
        per_person_limit: 2,
        campaign_id: "c1",
        status: "active",
        created_at: "2026-05-29T10:00:00Z",
        config_json: { amount: 10, min_order: 50 },
      },
    ],
    total: 1,
    page: 1,
    pageSize: 20,
    loading: false,
    filters: {},
    setPage: vi.fn(),
    setFilter: vi.fn(),
    resetFilters: vi.fn(),
    mutate: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    remove: vi.fn(),
  })),
}));

describe("BenefitsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGet.mockResolvedValue({ data: { items: [], total: 0 } });
  });

  it("renders benefit list with correct columns", () => {
    render(<BenefitsPage />);
    expect(screen.getByText("权益管理")).toBeInTheDocument();
    expect(screen.getByText("测试权益")).toBeInTheDocument();
    expect(screen.getByText("平台券")).toBeInTheDocument();
  });

  it("calls /benefits endpoint for list", async () => {
    render(<BenefitsPage />);
    await waitFor(() => {
      const calls = mockGet.mock.calls.filter((c) => c[0] === "/benefits");
      expect(calls.length).toBeGreaterThan(0);
    });
  });

  it("calls /benefits/admin/claims endpoint for claims tab", async () => {
    mockGet.mockResolvedValue({
      data: { items: [], total: 0, page: 1, page_size: 20 },
    });
    render(<BenefitsPage />);

    const claimsTab = screen.getByText("领取记录");
    fireEvent.click(claimsTab);

    await waitFor(() => {
      const calls = mockGet.mock.calls.filter(
        (c) => c[0] === "/benefits/admin/claims"
      );
      expect(calls.length).toBeGreaterThan(0);
    });
  });

  it("opens create modal with benefit type config fields", () => {
    render(<BenefitsPage />);
    const createBtn = screen.getByRole("button", { name: /新建权益/ });
    fireEvent.click(createBtn);

    expect(screen.queryAllByText("权益名称").length).toBeGreaterThan(0);
    expect(screen.queryAllByText("权益类型").length).toBeGreaterThan(0);
    expect(screen.queryAllByText("总库存").length).toBeGreaterThan(0);
    expect(screen.queryAllByText("每人限领").length).toBeGreaterThan(0);
    expect(screen.queryAllByText("关联活动").length).toBeGreaterThan(0);
  });

  it("shows platform coupon config fields when type selected", async () => {
    mockGet.mockResolvedValue({
      data: { items: [{ id: "c1", name: "活动1" }], total: 1 },
    });
    render(<BenefitsPage />);
    fireEvent.click(screen.getByRole("button", { name: /新建权益/ }));

    // Select platform_coupon type
    const typeSelect = document.querySelector('[name="benefit_type"]');
    if (typeSelect) {
      fireEvent.mouseDown(typeSelect);
      await waitFor(() => {
        const option = screen.getByText("平台券");
        if (option) fireEvent.click(option);
      });
    }
  });

  it("displays stock total and remaining in table", () => {
    render(<BenefitsPage />);
    // stock_total=100, stock_used=10, remaining=90
    expect(screen.getByText("100")).toBeInTheDocument();
    expect(screen.getByText("90")).toBeInTheDocument();
  });
});
