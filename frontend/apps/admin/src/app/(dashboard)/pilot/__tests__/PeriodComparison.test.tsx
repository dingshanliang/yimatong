import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

import PeriodComparison from "../_components/PeriodComparison";
import type {
  RetrospectiveRead,
  ScorecardSnapshot,
} from "../_components/types";

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
    claim_rate: { value: 10, status: "computed", unit: "percent" },
    wecom_rate: { value: 20, status: "computed", unit: "percent" },
    net_gmv: { value: 1000, status: "computed", unit: "yuan" },
    ...overrides,
  };
}

function retro(overrides: Partial<RetrospectiveRead> = {}): RetrospectiveRead {
  return {
    id: "r1",
    tenant_id: "t1",
    period_day: 7,
    window_start: "2026-07-01T00:00:00Z",
    window_end: "2026-07-08T00:00:00Z",
    next_review_date: "2026-07-14",
    status: "pending",
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

describe("PeriodComparison", () => {
  it("不足 2 期显示 Empty 提示", () => {
    render(<PeriodComparison retros={[retro()]} />);
    expect(screen.getByText("至少需要 2 期复盘才能对比")).toBeInTheDocument();
  });

  it("2 期显示对比区间与指标环比", () => {
    const r7 = retro({ period_day: 7 });
    const r14 = retro({
      id: "r2",
      period_day: 14,
      scorecard_snapshot: snapshot({
        valid_visits: { value: 150, status: "computed", unit: "count" },
      }),
    });
    render(<PeriodComparison retros={[r7, r14]} />);
    expect(screen.getByText("第 7 天 → 第 14 天")).toBeInTheDocument();
    expect(screen.getByText("有效访问")).toBeInTheDocument();
  });
});
