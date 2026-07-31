import { STATUS_COLORS } from "@/lib/status-colors";

export const DIRECTION_MAP: Record<string, { label: string; color: string }> = {
  outbound: { label: "推送到 CRM", color: STATUS_COLORS.processing },
  inbound: { label: "从 CRM 拉取", color: STATUS_COLORS.success },
  bidirectional: { label: "双向同步", color: STATUS_COLORS.warning },
};

export const SYNC_STATUS_MAP: Record<string, { label: string; color: string }> =
  {
    success: { label: "成功", color: STATUS_COLORS.success },
    failed: { label: "失败", color: STATUS_COLORS.error },
    pending: { label: "待处理", color: STATUS_COLORS.neutral },
    processing: { label: "处理中", color: STATUS_COLORS.processing },
  };
