import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import MilestoneTimeline from "../_components/MilestoneTimeline";
import type { DerivedDuration, MilestoneItem } from "../_components/types";

function milestone(overrides: Partial<MilestoneItem> = {}): MilestoneItem {
  return {
    type: "onboarding",
    label: "客户开通",
    status: "achieved",
    achieved_at: "2026-07-01T00:00:00Z",
    source: "tenant.created_at",
    ...overrides,
  };
}

describe("MilestoneTimeline", () => {
  it("渲染已达成里程碑及时间", () => {
    render(
      <MilestoneTimeline milestones={[milestone()]} derivedDurations={[]} />
    );
    expect(screen.getByText("客户开通")).toBeInTheDocument();
    // 未达成 tag 不应出现（已达成）
    expect(screen.queryByText("未达成")).not.toBeInTheDocument();
  });

  it("未达成里程碑显示'未达成' tag", () => {
    render(
      <MilestoneTimeline
        milestones={[
          milestone({
            status: "not_achieved",
            achieved_at: null,
            label: "正式上线",
          }),
        ]}
        derivedDurations={[]}
      />
    );
    expect(screen.getByText("正式上线")).toBeInTheDocument();
    expect(screen.getByText("未达成")).toBeInTheDocument();
  });

  it("派生时长：computed 显示天数，insufficient 显示'数据不足'", () => {
    const durations: DerivedDuration[] = [
      {
        label: "开通→上线时长",
        from_type: "onboarding",
        to_type: "launched",
        seconds: 86400 * 7,
        status: "computed",
      },
      {
        label: "上线→首扫时长",
        from_type: "launched",
        to_type: "first_valid_scan",
        seconds: null,
        status: "insufficient_data",
      },
    ];
    render(
      <MilestoneTimeline
        milestones={[milestone()]}
        derivedDurations={durations}
      />
    );
    expect(screen.getByText(/开通→上线时长：7\.0 天/)).toBeInTheDocument();
    expect(screen.getByText(/上线→首扫时长：数据不足/)).toBeInTheDocument();
  });

  it("空里程碑显示空态", () => {
    render(<MilestoneTimeline milestones={[]} derivedDurations={[]} />);
    expect(screen.getByText("暂无里程碑数据")).toBeInTheDocument();
  });
});
