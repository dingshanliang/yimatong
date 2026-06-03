/**
 * Shared constants for the platform admin app.
 * Extracted from duplicated definitions across multiple page components.
 */

export const STATUS_MAP: Record<string, { color: string; label: string }> = {
  active: { color: "green", label: "活跃" },
  suspended: { color: "orange", label: "暂停" },
  terminated: { color: "red", label: "已终止" },
};

export const PLAN_MAP: Record<string, { color: string; label: string }> = {
  free: { color: "default", label: "免费版" },
  starter: { color: "blue", label: "入门版" },
  pro: { color: "purple", label: "专业版" },
  enterprise: { color: "gold", label: "企业版" },
};

export const ACTION_COLORS: Record<string, string> = {
  create_tenant: "green",
  update_tenant: "blue",
  delete_tenant: "red",
  status_change: "orange",
  assign_plan: "purple",
  create_plan: "cyan",
  update_plan: "geekblue",
  update_config: "gold",
  refresh_health: "lime",
};
