import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import MembersPage from "./page";

const mockGet = vi.fn();
const mockPost = vi.fn();
const mockPatch = vi.fn();
const mockMessageError = vi.fn();
const mockMessageInfo = vi.fn();
const mockMessageSuccess = vi.fn();
const mockMessage = {
  error: mockMessageError,
  info: mockMessageInfo,
  success: mockMessageSuccess,
};

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
    patch: (...args: unknown[]) => mockPatch(...args),
  },
}));

vi.mock("antd", async () => {
  const actual = await vi.importActual<typeof import("antd")>("antd");
  return {
    ...actual,
    App: {
      useApp: () => ({
        message: mockMessage,
      }),
    },
  };
});

describe("MembersPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGet.mockImplementation((url: string) => {
      if (url === "/members/repurchase-workbench") {
        return Promise.resolve({
          data: {
            metrics: [
              {
                key: "new_members",
                label: "新增正式会员",
                value: 12,
                availability: "complete",
                coverage_status: "unavailable",
              },
              {
                key: "mature_members",
                label: "成熟观察会员",
                value: 8,
                availability: "complete",
                coverage_status: "unavailable",
              },
              {
                key: "repurchase_members",
                label: "产生复购的会员",
                value: null,
                availability: "unavailable",
                coverage_status: "unavailable",
              },
              {
                key: "member_scan_repurchase_rate_percent",
                label: "会员整体扫码复购率",
                value: null,
                availability: "unavailable",
                coverage_status: "unavailable",
              },
              {
                key: "packaging_repurchase_orders",
                label: "包装扫码复购订单数",
                value: null,
                availability: "unavailable",
                coverage_status: "unavailable",
              },
              {
                key: "attributed_net_sales_fen",
                label: "归因净销售额",
                value: null,
                availability: "unavailable",
                coverage_status: "unavailable",
              },
            ],
            operations: {},
            source_coverage: {
              status: "unavailable",
              active_connections: 0,
              incomplete_orders: 0,
            },
            trust_rubric: [],
            recent_orders: [],
            work_items: [],
          },
        });
      }
      if (url === "/members/consumers/search") {
        return Promise.resolve({
          data: {
            items: [
              {
                id: "018f0c86-4c11-7b31-aeb7-94a11b229fd0",
                nickname: "王女士",
                phone: "138****8000",
                membership: {
                  id: "018f0c86-4c11-7b31-aeb7-94a11b229fd1",
                  number: "MBR-202608200001",
                  status: "active",
                  joined_at: "2026-08-20T08:00:00Z",
                },
              },
            ],
          },
        });
      }
      return Promise.reject(new Error("unexpected endpoint"));
    });
  });

  it("shows formal membership facts without reviving the first-phase points UI", async () => {
    render(<MembersPage />);

    expect(await screen.findByText("新增正式会员")).toBeInTheDocument();
    expect(screen.getByText("12")).toBeInTheDocument();
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
    expect(screen.getByRole("tab", { name: "品牌会员" })).toBeInTheDocument();
    expect(
      screen.getByRole("tab", { name: "复购券与触达" })
    ).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "订单与退款" })).toBeInTheDocument();
    expect(
      screen.getByRole("tab", { name: "归因与数据质量" })
    ).toBeInTheDocument();
    expect(screen.queryByText("积分规则")).not.toBeInTheDocument();
    expect(screen.queryByText("积分商城")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "品牌会员" }));
    fireEvent.change(
      screen.getByPlaceholderText("输入会员编号、手机号或昵称"),
      {
        target: { value: "MBR-202608200001" },
      }
    );
    fireEvent.click(screen.getByRole("button", { name: /查询/ }));

    await waitFor(() =>
      expect(screen.getAllByText("MBR-202608200001").length).toBeGreaterThan(0)
    );
    expect(screen.getAllByText("王女士").length).toBeGreaterThan(0);
    expect(
      screen.queryByText("018f0c86-4c11-7b31-aeb7-94a11b229fd0")
    ).not.toBeInTheDocument();
  });
});
