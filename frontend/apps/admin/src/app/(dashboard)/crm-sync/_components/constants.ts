export const DIRECTION_MAP: Record<string, { label: string; color: string }> = {
  outbound: { label: "推送到 CRM", color: "#1d4ed8" },
  inbound: { label: "从 CRM 拉取", color: "#16a34a" },
  bidirectional: { label: "双向同步", color: "#f59e0b" },
};

export const SYNC_STATUS_MAP: Record<string, { label: string; color: string }> =
  {
    success: { label: "成功", color: "#16a34a" },
    failed: { label: "失败", color: "#b91c1c" },
    pending: { label: "待处理", color: "#8c8c8c" },
    processing: { label: "处理中", color: "#1d4ed8" },
  };
