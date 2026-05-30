export const EVENT_OPTIONS = [
  { value: "scan.created", label: "扫码事件" },
  { value: "claim.created", label: "领券事件" },
  { value: "claim.used", label: "核销事件" },
  { value: "claim.expired", label: "过期事件" },
  { value: "consumer.created", label: "新消费者" },
  { value: "consumer.profile_updated", label: "信息变更" },
  { value: "risk.alert", label: "风险告警" },
  { value: "campaign.started", label: "活动开始" },
  { value: "campaign.ended", label: "活动结束" },
];

export const ROLE_OPTIONS = [
  { value: "data_reader", label: "数据只读 (data_reader)" },
  { value: "coupon_operator", label: "券操作 (coupon_operator)" },
  { value: "webhook_admin", label: "Webhook 管理 (webhook_admin)" },
  { value: "full_access", label: "完全访问 (full_access)" },
];

export const STATUS_COLORS: Record<string, string> = {
  delivered: "green",
  pending: "blue",
  retrying: "orange",
  failed: "red",
};
