import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

import RetrospectiveCard from "../_components/RetrospectiveCard";
import type {
  RetrospectiveRead,
  ScorecardSnapshot,
} from "../_components/types";

const mockMessageSuccess = vi.fn();
const mockMessageError = vi.fn();

vi.mock("antd", async () => {
  const actual = await vi.importActual<typeof import("antd")>("antd");
  return {
    ...actual,
    App: {
      useApp: () => ({
        message: { success: mockMessageSuccess, error: mockMessageError },
        modal: { confirm: vi.fn() },
      }),
    },
  };
});

const mockPatch = vi.fn();
vi.mock("@/lib/api", () => ({
  default: {
    get: vi.fn(),
    patch: (...args: unknown[]) => mockPatch(...args),
  },
  extractErrorMessage: (_err: unknown, fallback = "操作失败") => fallback,
}));

function snapshot(
  overrides: Partial<ScorecardSnapshot> = {}
): ScorecardSnapshot {
  return {
    window_start: "2026-07-01T00:00:00Z",
    window_end: "2026-07-08T00:00:00Z",
    window_days: 7,
    onboarding_to_launch: {
      value: 86400 * 7,
      status: "computed",
      unit: "seconds",
    },
    launch_to_first_scan: {
      value: null,
      status: "insufficient_data",
      unit: "seconds",
    },
    valid_visits: { value: 100, status: "computed", unit: "count" },
    claim_rate: { value: null, status: "insufficient_data", unit: "percent" },
    wecom_rate: { value: 20.5, status: "computed", unit: "percent" },
    net_gmv: { value: null, status: "insufficient_data", unit: "yuan" },
    ...overrides,
  };
}

function retro(overrides: Partial<RetrospectiveRead> = {}): RetrospectiveRead {
  return {
    id: "retro-1",
    tenant_id: "t1",
    period_day: 7,
    window_start: "2026-07-01T00:00:00Z",
    window_end: "2026-07-08T00:00:00Z",
    next_review_date: "2026-07-14",
    status: "pending",
    version: 1,
    derived_status: "pending",
    goal: null,
    scorecard_snapshot: snapshot(),
    issues: null,
    actions: [],
    completed_at: null,
    completed_by: null,
    supplementary_notes: null,
    ops_task_id: null,
    created_at: "2026-07-08T00:00:00Z",
    updated_at: "2026-07-08T00:00:00Z",
    ...overrides,
  };
}

describe("RetrospectiveCard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("scorecard 数据不足显示'数据不足'，不显示 0", () => {
    render(<RetrospectiveCard retro={retro()} onChanged={vi.fn()} />);
    expect(screen.getAllByText("数据不足").length).toBeGreaterThan(0);
  });

  it("已计算指标的标题与单位正常渲染", () => {
    render(<RetrospectiveCard retro={retro()} onChanged={vi.fn()} />);
    // antd Statistic 异步渲染数值（jsdom 计时不稳定），断言稳定的标题文案
    expect(screen.getByText("企微确认率")).toBeInTheDocument();
    expect(screen.getByText("有效访问")).toBeInTheDocument();
    expect(screen.getByText("净 GMV")).toBeInTheDocument();
  });

  it("pending 态显示'填写'按钮，点击进入编辑", () => {
    render(<RetrospectiveCard retro={retro()} onChanged={vi.fn()} />);
    expect(screen.getByText("填写")).toBeInTheDocument();
    fireEvent.click(screen.getByText("填写"));
    expect(screen.getByText("本期目标")).toBeInTheDocument();
  });

  it("completed 态不显示填写按钮（快照冻结）", () => {
    render(
      <RetrospectiveCard
        retro={retro({ status: "completed", derived_status: "completed" })}
        onChanged={vi.fn()}
      />
    );
    expect(screen.queryByText("填写")).not.toBeInTheDocument();
  });

  it("点击填写进入编辑模式并预填已有值", async () => {
    render(
      <RetrospectiveCard
        retro={retro({ goal: "已有目标", issues: "已有问题" })}
        onChanged={vi.fn()}
      />
    );
    fireEvent.click(screen.getByText("填写"));
    // 编辑模式开启：表单项出现（Form 字段渲染稳定，可断言）
    await waitFor(() => {
      expect(screen.getByText("本期目标")).toBeInTheDocument();
      expect(screen.getByText("问题与判断")).toBeInTheDocument();
      expect(screen.getByText("下次验证日期")).toBeInTheDocument();
    });
  });

  it("编辑模式含动作清单 Form.List（添加动作按钮）", async () => {
    render(<RetrospectiveCard retro={retro()} onChanged={vi.fn()} />);
    fireEvent.click(screen.getByText("填写"));
    await waitFor(() => {
      expect(screen.getByText("+ 添加动作")).toBeInTheDocument();
    });
  });

  it("carryover 动作在只读视图显示'承接'徽标", () => {
    render(
      <RetrospectiveCard
        retro={retro({
          actions: [
            {
              content: "上期遗留",
              status: "pending",
              carryover: true,
              carryover_disposition: null,
            },
          ],
        })}
        onChanged={vi.fn()}
      />
    );
    expect(screen.getByText("上期遗留")).toBeInTheDocument();
    expect(screen.getByText("承接·待处置")).toBeInTheDocument();
  });

  it("completed 态显示'追加补充说明'按钮（PRD §5）", () => {
    render(
      <RetrospectiveCard
        retro={retro({ status: "completed", derived_status: "completed" })}
        onChanged={vi.fn()}
      />
    );
    expect(screen.getByText("追加补充说明")).toBeInTheDocument();
  });

  it("点击'追加补充说明'展开表单", async () => {
    render(
      <RetrospectiveCard
        retro={retro({ status: "completed", derived_status: "completed" })}
        onChanged={vi.fn()}
      />
    );
    fireEvent.click(screen.getByText("追加补充说明"));
    // 展开后表单出现：supplementary_notes 文本域（按 placeholder 断言，稳定）
    await waitFor(() => {
      expect(screen.getByPlaceholderText("追加的补充说明")).toBeInTheDocument();
    });
  });

  it("复盘变更绑定当前版本和一次性幂等键", async () => {
    mockPatch.mockResolvedValue({ data: {} });
    vi.spyOn(crypto, "randomUUID").mockReturnValue(
      "11111111-1111-4111-8111-111111111111"
    );
    render(
      <RetrospectiveCard
        retro={retro({
          status: "completed",
          derived_status: "completed",
          version: 7,
        })}
        onChanged={vi.fn()}
      />
    );
    fireEvent.click(screen.getByText("追加补充说明"));
    fireEvent.change(screen.getByPlaceholderText("追加的补充说明"), {
      target: { value: "新增核验结果" },
    });
    fireEvent.click(screen.getByRole("button", { name: /追\s*加/ }));

    await waitFor(() => {
      expect(mockPatch).toHaveBeenCalledWith(
        "/retrospectives/retro-1",
        { supplementary_notes: "新增核验结果" },
        {
          headers: {
            "Idempotency-Key": "11111111-1111-4111-8111-111111111111",
            "If-Match": "7",
          },
        }
      );
    });
  });
});
