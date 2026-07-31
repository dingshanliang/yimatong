/**
 * Shared constants for the platform admin app.
 * Extracted from duplicated definitions across multiple page components.
 */

import { STATUS_COLORS } from "./status-colors";

export const STATUS_MAP: Record<string, { color: string; label: string }> = {
  active: { color: STATUS_COLORS.success, label: "活跃" },
  suspended: { color: STATUS_COLORS.warning, label: "暂停" },
  terminated: { color: STATUS_COLORS.error, label: "已终止" },
};

export const PLAN_MAP: Record<string, { color: string; label: string }> = {
  free: { color: STATUS_COLORS.neutral, label: "免费版" },
  starter: { color: STATUS_COLORS.processing, label: "入门版" },
  pro: { color: STATUS_COLORS.warning, label: "专业版" },
  enterprise: { color: STATUS_COLORS.warning, label: "企业版" },
};

export const ACTION_COLORS: Record<string, string> = {
  create_tenant: STATUS_COLORS.success,
  update_tenant: STATUS_COLORS.processing,
  delete_tenant: STATUS_COLORS.error,
  status_change: STATUS_COLORS.warning,
  assign_plan: STATUS_COLORS.warning,
  create_plan: STATUS_COLORS.processing,
  update_plan: STATUS_COLORS.processing,
  update_config: STATUS_COLORS.warning,
  refresh_health: STATUS_COLORS.success,
};
