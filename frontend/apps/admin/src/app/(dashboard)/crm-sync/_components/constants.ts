export const DIRECTION_MAP: Record<string, { label: string; color: string }> = {
  outbound: { label: "推送到 CRM", color: "blue" },
  inbound: { label: "从 CRM 拉取", color: "green" },
  bidirectional: { label: "双向同步", color: "purple" },
};

export const SYNC_STATUS_MAP: Record<string, { label: string; color: string }> = {
  success: { label: "成功", color: "green" },
  failed: { label: "失败", color: "red" },
  pending: { label: "待处理", color: "default" },
  processing: { label: "处理中", color: "blue" },
};
