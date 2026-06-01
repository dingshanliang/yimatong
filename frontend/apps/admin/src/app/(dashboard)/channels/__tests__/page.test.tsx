import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import ChannelsPage from "../page";

const mockMessageSuccess = vi.fn();
const mockMessageError = vi.fn();
const mockModalConfirm = vi.fn();

vi.mock("antd", async () => {
  const actual = await vi.importActual<typeof import("antd")>("antd");
  return {
    ...actual,
    App: {
      useApp: () => ({
        message: { success: mockMessageSuccess, error: mockMessageError },
        modal: { confirm: mockModalConfirm },
      }),
    },
  };
});

const mockGet = vi.fn();
const mockPost = vi.fn();
const mockPatch = vi.fn();
const mockPut = vi.fn();
const mockDelete = vi.fn();

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
    patch: (...args: unknown[]) => mockPatch(...args),
    put: (...args: unknown[]) => mockPut(...args),
    delete: (...args: unknown[]) => mockDelete(...args),
  },
  extractErrorMessage: (_err: unknown, fallback = "操作失败") => fallback,
}));

function paginated(items: unknown[]) {
  return { data: { items, total: items.length, page: 1, page_size: 20 } };
}

describe("ChannelsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGet.mockImplementation((url: string) => {
      if (url === "/channels/overview") {
        return Promise.resolve({
          data: {
            distributor_count: 2,
            region_count: 3,
            store_count: 5,
            allocated_quantity: 1200,
            pending_diversion_count: 4,
          },
        });
      }
      if (url === "/channels/distributors") {
        return Promise.resolve(
          paginated([
            {
              id: "d1",
              name: "华东经销商",
              code: "DIST-EAST",
              status: "active",
              region_count: 2,
              store_count: 4,
              allocated_quantity: 800,
            },
          ]),
        );
      }
      if (url === "/channels/regions") {
        return Promise.resolve(
          paginated([{ id: "r1", name: "上海区域", code: "REG-SH", city: "上海", distributor_name: "华东经销商", store_count: 2 }]),
        );
      }
      if (url === "/channels/stores") {
        return Promise.resolve(
          paginated([
            {
              id: "s1",
              name: "南京东路店",
              code: "STORE-NJDL",
              region_name: "上海区域",
              distributor_name: "华东经销商",
              allocated_quantity: 300,
              status: "active",
            },
          ]),
        );
      }
      if (url === "/code-batches") {
        return Promise.resolve(paginated([{ id: "b1", batch_code: "CB-001", quantity: 1000, product_name: "有机大米" }]));
      }
      if (url === "/channels/code-allocations") {
        return Promise.resolve(
          paginated([
            {
              id: "a1",
              batch_code: "CB-001",
              product_name: "有机大米",
              sku_name: "5kg",
              store_name: "南京东路店",
              distributor_name: "华东经销商",
              region_name: "上海区域",
              quantity: 300,
              batch_quantity: 1000,
              remaining_quantity: 700,
              allocated_at: "2026-06-01T10:00:00Z",
            },
          ]),
        );
      }
      if (url === "/channels/diversion-clues") {
        return Promise.resolve(
          paginated([
            {
              id: "c1",
              public_id: "QR001",
              expected_region: "上海",
              detected_city: "北京",
              distributor_id: "d1",
              resolved: false,
              resolution_note: null,
            },
          ]),
        );
      }
      if (url === "/accounts") {
        return Promise.resolve({ data: [{ id: "acc1", name: "经销商账号", email: "dist@test.com" }] });
      }
      if (url === "/channels/account-scopes") {
        return Promise.resolve({ data: [] });
      }
      return Promise.resolve(paginated([]));
    });
    mockPatch.mockResolvedValue({ data: {} });
    mockPost.mockResolvedValue({ data: {} });
    mockPut.mockResolvedValue({ data: {} });
  });

  it("renders overview metrics and business-readable master data", async () => {
    render(<ChannelsPage />);

    expect(screen.getByText("渠道管理")).toBeInTheDocument();
    await waitFor(() => expect(screen.getAllByText("已分配码量").length).toBeGreaterThan(0));
    expect(screen.getByText("华东经销商")).toBeInTheDocument();
    expect(screen.getByText("2 个区域 / 4 个门店")).toBeInTheDocument();
    expect(screen.getByText("800 个码")).toBeInTheDocument();
  });

  it("shows allocation records with business names instead of UUID-only values", async () => {
    render(<ChannelsPage />);
    fireEvent.click(screen.getByRole("tab", { name: "码段分配" }));

    await waitFor(() => expect(screen.getByText("CB-001")).toBeInTheDocument());
    expect(screen.getByText("有机大米 / 5kg")).toBeInTheDocument();
    expect(screen.getByText("南京东路店")).toBeInTheDocument();
    expect(screen.getByText("剩余 700")).toBeInTheDocument();
  });

  it("opens diversion detail drawer and resolves with note", async () => {
    render(<ChannelsPage />);
    fireEvent.click(screen.getByRole("tab", { name: "窜货线索" }));

    fireEvent.click(await screen.findByRole("button", { name: /处理/ }));
    expect(await screen.findByText("窜货线索处理")).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText("填写处理记录"), { target: { value: "已联系经销商核实" } });
    fireEvent.click(screen.getByRole("button", { name: /标记为已处理/ }));

    await waitFor(() =>
      expect(mockPut).toHaveBeenCalledWith("/risk-dashboard/diversion-clues/c1/resolve", {
        resolution_note: "已联系经销商核实",
      }),
    );
  });
});
