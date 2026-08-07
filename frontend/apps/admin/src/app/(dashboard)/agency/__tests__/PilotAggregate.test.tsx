import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";

import PilotAggregate from "../_components/PilotAggregate";

const mockGet = vi.fn();
const mockSwitchAgencyContext = vi.fn();

vi.mock("antd", async () => {
  const actual = await vi.importActual<typeof import("antd")>("antd");
  return {
    ...actual,
    App: {
      useApp: () => ({
        message: { success: vi.fn(), error: vi.fn() },
        modal: { confirm: vi.fn() },
      }),
    },
  };
});

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
  },
  extractErrorMessage: (_err: unknown, fallback = "操作失败") => fallback,
}));

vi.mock("@/lib/auth", () => ({
  useAuthStore: (selector: (s: { switchAgencyContext: unknown }) => unknown) =>
    selector({ switchAgencyContext: mockSwitchAgencyContext }),
}));

const aggregateData = {
  summary: { total_clients: 2, clients_with_pending_retros: 1 },
  clients: [
    {
      client_id: "c1",
      client_name: "客户甲",
      client_slug: "c-jia",
      milestone_summary: { achieved_count: 4, total: 5 },
      pending_retrospectives: [
        {
          retro_id: "r1",
          period_day: 7,
          next_review_date: "2026-07-14",
          derived_status: "pending",
        },
      ],
      full_pilot_access: true,
    },
    {
      client_id: "c2",
      client_name: "客户乙",
      client_slug: "c-yi",
      milestone_summary: { achieved_count: 1, total: 5 },
      pending_retrospectives: [],
      full_pilot_access: true,
    },
  ],
};

describe("PilotAggregate", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGet.mockResolvedValue({ data: aggregateData });
  });

  it("渲染聚合表：客户名 + 里程碑达成 + 待办复盘", async () => {
    render(<PilotAggregate />);
    await waitFor(() => {
      expect(screen.getByText("客户甲")).toBeInTheDocument();
      expect(screen.getByText("客户乙")).toBeInTheDocument();
    });
    // 里程碑达成 4/5、1/5
    expect(screen.getByText("4/5")).toBeInTheDocument();
    expect(screen.getByText("1/5")).toBeInTheDocument();
  });

  it("待办复盘显示期次 Tag（第7天·待填写）", async () => {
    render(<PilotAggregate />);
    await waitFor(() => {
      expect(screen.getByText(/第7天/)).toBeInTheDocument();
      expect(screen.getByText(/待填写/)).toBeInTheDocument();
    });
  });

  it("点击'进入客户试点'触发 switchAgencyContext", async () => {
    render(<PilotAggregate />);
    await waitFor(() => expect(screen.getByText("客户甲")).toBeInTheDocument());
    fireEvent.click(screen.getAllByText("进入客户试点")[0]);
    expect(mockSwitchAgencyContext).toHaveBeenCalledWith("c1");
  });

  it("加载失败显示错误（不崩溃）", async () => {
    mockGet.mockRejectedValue(new Error("网络错误"));
    render(<PilotAggregate />);
    await waitFor(() => {
      // 失败后 data 为 null → 显示空态
      expect(screen.getByText("暂无授权客户的试点数据")).toBeInTheDocument();
    });
  });
});
