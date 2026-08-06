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

export interface ActionItem {
  content: string;
  owner_id?: string | null;
  due_date?: string | null;
  status?: string;
}

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
