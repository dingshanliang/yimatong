/**
 * Shared constants for the platform admin app.
 * Extracted from duplicated definitions across multiple page components.
 */

export const STATUS_MAP: Record<string, { color: string; label: string }> = {
  active: { color: "#16a34a", label: "活跃" },
  suspended: { color: "#f59e0b", label: "暂停" },
  terminated: { color: "#b91c1c", label: "已终止" },
};

export const PLAN_MAP: Record<string, { color: string; label: string }> = {
  free: { color: "#8c8c8c", label: "免费版" },
  starter: { color: "#1d4ed8", label: "入门版" },
  pro: { color: "#f59e0b", label: "专业版" },
  enterprise: { color: "#f59e0b", label: "企业版" },
};

export const ACTION_COLORS: Record<string, string> = {
  create_tenant: "#16a34a",
  update_tenant: "#1d4ed8",
  delete_tenant: "#b91c1c",
  status_change: "#f59e0b",
  assign_plan: "#f59e0b",
  create_plan: "#1d4ed8",
  update_plan: "#1d4ed8",
  update_config: "#f59e0b",
  refresh_health: "#16a34a",
};
