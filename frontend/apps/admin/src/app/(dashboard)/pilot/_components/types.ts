/**
 * 试点里程碑与复盘前端类型 + 状态映射（beads: yimatong-bgag.5）。
 * 镜像后端 app/schemas/pilot_milestone.py + retrospective.py。
 */

import { STATUS_COLORS, STATUS_TOKEN_COLORS } from "@/lib/status-colors";

/** antd Tag color preset 值（STATUS_COLORS 的值类型）。 */
type StatusPreset = (typeof STATUS_COLORS)[keyof typeof STATUS_COLORS];

/** STATUS_TOKEN_COLORS 的 key（语义色名）。 */
type StatusTokenKey = keyof typeof STATUS_TOKEN_COLORS;

/** 里程碑展示状态（后端 PilotMilestoneStatus + insufficient）。 */
export type MilestoneStatus = "achieved" | "not_achieved" | "insufficient_data";

export interface MilestoneItem {
  type: string;
  label: string;
  status: MilestoneStatus;
  achieved_at: string | null;
  source: string | null;
}

export interface DerivedDuration {
  label: string;
  from_type: string;
  to_type: string;
  seconds: number | null;
  status: string; // computed | insufficient_data
}

export interface MilestoneTimelineResponse {
  tenant_id: string;
  milestones: MilestoneItem[];
  derived_durations: DerivedDuration[];
}

/** 复盘存储态。 */
export type RetrospectiveStatus = "pending" | "completed";
/** 复盘派生态（含逾期）。 */
export type RetroDerivedStatus =
  RetrospectiveStatus | "overdue" | "overdue_completed";

export interface ScorecardMetric {
  value: number | null;
  status: string; // computed | insufficient_data
  unit: string | null;
  denominator?: number;
  numerator?: number;
}

export interface ScorecardSnapshot {
  window_start: string;
  window_end: string;
  window_days: number;
  onboarding_to_launch: ScorecardMetric;
  launch_to_first_scan: ScorecardMetric;
  valid_visits: ScorecardMetric;
  claim_rate: ScorecardMetric;
  wecom_rate: ScorecardMetric;
  net_gmv: ScorecardMetric;
  funnel_raw?: Record<string, number>;
  [key: string]: unknown;
}

/** 动作承接处置（镜像后端 ACTION_DISPOSITIONS，PRD §8）。 */
export type ActionDisposition = "continue" | "adjust" | "abandon";

export interface ActionItem {
  content: string;
  owner_id?: string | null;
  due_date?: string | null;
  status?: string;
  /** PRD §8：上期承接标记 + 处置（continue/adjust/abandon）。 */
  carryover?: boolean;
  carryover_disposition?: ActionDisposition | null;
}

/** 动作承接处置选项（镜像后端，供 Radio.Group 使用）。 */
export const ACTION_DISPOSITION_OPTIONS: {
  value: ActionDisposition;
  label: string;
}[] = [
  { value: "continue", label: "继续" },
  { value: "adjust", label: "调整" },
  { value: "abandon", label: "放弃" },
];

export interface RetrospectiveRead {
  id: string;
  tenant_id: string;
  period_day: number;
  window_start: string;
  window_end: string;
  next_review_date: string;
  status: RetrospectiveStatus;
  derived_status: RetroDerivedStatus;
  goal: string | null;
  scorecard_snapshot: ScorecardSnapshot;
  issues: string | null;
  actions: ActionItem[];
  completed_at: string | null;
  completed_by: string | null;
  supplementary_notes: string | null;
  ops_task_id: string | null;
  created_at: string;
  updated_at: string;
}

/** 复盘派生态 → antd Tag preset 名（STATUS_COLORS 单一事实来源）。 */
export const RETRO_STATUS_TAG: Record<RetroDerivedStatus, StatusPreset> = {
  pending: STATUS_COLORS.processing,
  completed: STATUS_COLORS.success,
  overdue: STATUS_COLORS.error,
  overdue_completed: STATUS_COLORS.warning,
};

/** 复盘派生态 → 中文标签。 */
export const RETRO_STATUS_LABEL: Record<RetroDerivedStatus, string> = {
  pending: "待填写",
  completed: "已完成",
  overdue: "逾期",
  overdue_completed: "逾期完成",
};

/** 里程碑状态 → Timeline color（用 STATUS_TOKEN_COLORS 的 key）。 */
export const MILESTONE_COLOR_KEY: Record<MilestoneStatus, StatusTokenKey> = {
  achieved: "success",
  not_achieved: "neutral",
  insufficient_data: "warning",
};

/** 秒数 → 可读时长（天/小时/秒）。null → "数据不足"。里程碑派生时长与 scorecard 共用。 */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "数据不足";
  const days = seconds / 86400;
  if (days >= 1) return `${days.toFixed(1)} 天`;
  const hours = seconds / 3600;
  if (hours >= 1) return `${hours.toFixed(1)} 小时`;
  return `${seconds.toFixed(0)} 秒`;
}

/** 相邻期对比：两期 scorecard 之间 6 指标的环比（PRD §4.5）。
 * 返回每指标的 delta（后值 − 前值）；任一期数据不足则 delta=null。 */
export interface MetricDelta {
  key: string;
  label: string;
  delta: number | null; // 正=改善，负=恶化；null=数据不足
}

const COMPARISON_METRICS: { key: string; label: string }[] = [
  { key: "valid_visits", label: "有效访问" },
  { key: "claim_rate", label: "权益确认率" },
  { key: "wecom_rate", label: "企微确认率" },
  { key: "net_gmv", label: "净 GMV" },
];

/** 计算两期 scorecard 的指标环比（PRD §4.5）。 */
export function computeMetricDeltas(
  prev: ScorecardSnapshot,
  curr: ScorecardSnapshot
): MetricDelta[] {
  return COMPARISON_METRICS.map(({ key, label }) => {
    const p = prev[key] as ScorecardMetric | undefined;
    const c = curr[key] as ScorecardMetric | undefined;
    if (
      !p ||
      !c ||
      p.status === "insufficient_data" ||
      c.status === "insufficient_data" ||
      p.value === null ||
      p.value === undefined ||
      c.value === null ||
      c.value === undefined
    ) {
      return { key, label, delta: null };
    }
    return { key, label, delta: (c.value as number) - (p.value as number) };
  });
}

/** 上期动作完成率（PRD §4.5）。无动作返回 null。 */
export function actionCompletionRate(actions: ActionItem[]): number | null {
  if (!actions || actions.length === 0) return null;
  const completed = actions.filter((a) => a.status === "completed").length;
  return (completed / actions.length) * 100;
}

/** delta → 可读文案（含正负号）。null → "数据不足"。 */
export function formatDelta(delta: number | null): string {
  if (delta === null) return "数据不足";
  const sign = delta > 0 ? "+" : "";
  return `${sign}${delta.toFixed(2)}`;
}
